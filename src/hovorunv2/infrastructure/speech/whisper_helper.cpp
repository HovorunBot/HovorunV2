
// Dedicated helper for whisper.cpp model execution over anonymous pipes.
#include "whisper.h"

#include <algorithm>
#include <cerrno>
#include <cstddef>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <poll.h>
#include <string>
#include <sys/prctl.h>
#include <signal.h>
#include <unistd.h>
#include <vector>

constexpr uint32_t max_samples = WHISPER_SAMPLE_RATE * 120;
constexpr size_t max_output_bytes = 128000;
constexpr int compute_threads = 2;

void discard_log(enum ggml_log_level, const char *, void *) {}

bool deny_network() {
    sock_filter rules[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_socket, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_socketpair, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_connect, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    sock_fprog program{static_cast<unsigned short>(sizeof(rules) / sizeof(rules[0])), rules};
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) return false;
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program) != 0) return false;
    errno = 0;
    return socket(AF_INET, SOCK_STREAM, 0) == -1 && errno == EPERM;
}

bool read_exact(char * target, size_t bytes) {
    size_t offset = 0;
    while (offset < bytes) {
        const auto result = ::read(STDIN_FILENO, target + offset, bytes - offset);
        if (result <= 0) return false;
        offset += static_cast<size_t>(result);
    }
    return true;
}

std::string escape_json(const std::string & text) {
    std::string output;
    for (const unsigned char character : text) {
        if (character == '"' || character == '\\') {
            output += '\\';
            output += character;
        } else if (character < 0x20) {
            output += ' ';
        } else {
            output += character;
        }
    }
    return output;
}

bool write_result(const std::string & result) {
    if (result.size() > max_output_bytes) return false;
    const uint32_t length = result.size();
    std::cout.write(reinterpret_cast<const char *>(&length), sizeof(length));
    std::cout.write(result.data(), result.size());
    std::cout.flush();
    return std::cout.good();
}

std::string recognize(whisper_context * context, const std::vector<float> & samples) {
    double energy = 0;
    for (float sample : samples) {
        if (!std::isfinite(sample)) return "{\"status\":\"invalid_audio\"}";
        energy += static_cast<double>(sample) * sample;
    }
    if (energy / samples.size() < 1e-10) return "{\"status\":\"no_speech\"}";
    auto state = std::unique_ptr<whisper_state, decltype(&whisper_free_state)>(
        whisper_init_state(context), whisper_free_state);
    if (!state) return "{\"status\":\"state_failed\"}";
    auto parameters = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    parameters.n_threads = compute_threads;
    parameters.greedy.best_of = 1;
    parameters.temperature_inc = 0;
    parameters.language = "auto";
    parameters.translate = false;
    parameters.no_context = true;
    parameters.print_progress = false;
    parameters.print_realtime = false;
    parameters.print_timestamps = false;
    parameters.print_special = false;
    parameters.suppress_nst = true;
    if (whisper_full_with_state(context, state.get(), parameters, samples.data(), samples.size()) != 0)
        return "{\"status\":\"inference_failed\"}";
    std::string text;
    for (int index = 0; index < whisper_full_n_segments_from_state(state.get()); ++index) {
        text += whisper_full_get_segment_text_from_state(state.get(), index);
        if (text.size() > max_output_bytes / 2) return "{\"status\":\"output_limit\"}";
    }
    if (text.find_first_not_of(" \t\r\n.,!?") == std::string::npos)
        return "{\"status\":\"no_speech\"}";
    const char * language = whisper_lang_str(whisper_full_lang_id_from_state(state.get()));
    return "{\"status\":\"transcript\",\"language\":\"" + std::string(language ? language : "und") +
        "\",\"text\":\"" + escape_json(text) + "\"}";
}

int main(int count, char ** arguments) {
    if (count != 3) return 2;
    // Parent loss always stops the model owner; close the registration race as well.
    const auto parent = getppid();
    if (prctl(PR_SET_PDEATHSIG, SIGKILL) != 0 || getppid() != parent || parent == 1) return 3;
    const int idle_ms = std::stoi(arguments[2]);
    if (idle_ms <= 0) return 4;
    if (!deny_network()) return 12;
    whisper_log_set(discard_log, nullptr);
    auto options = whisper_context_default_params();
    options.use_gpu = false;
    options.flash_attn = true;
    const auto started = std::chrono::steady_clock::now();
    auto context = std::unique_ptr<whisper_context, decltype(&whisper_free)>(
        whisper_init_from_file_with_params_no_state(arguments[1], options), whisper_free);
    if (!context) return 5;
    const auto load_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - started).count();
    if (!write_result("{\"status\":\"ready\",\"network_blocked\":true,\"load_ms\":" + std::to_string(load_ms) + "}")) return 6;
    while (true) {
        pollfd input{STDIN_FILENO, POLLIN, 0};
        const int available = poll(&input, 1, idle_ms);
        if (available == 0) return 0;
        if (available < 0 || !(input.revents & POLLIN)) return 7;
        uint32_t length = 0;
        if (!read_exact(reinterpret_cast<char *>(&length), sizeof(length))) return 8;
        if (length == 0 || length > max_samples * sizeof(float) || length % sizeof(float) != 0) {
            write_result("{\"status\":\"invalid_frame\"}");
            return 9;
        }
        {
            std::vector<float> samples(length / sizeof(float));
            if (!read_exact(reinterpret_cast<char *>(samples.data()), length)) return 10;
            std::string result = recognize(context.get(), samples);
            if (!write_result(result)) return 11;
            std::fill(result.begin(), result.end(), '\0');
            std::fill(samples.begin(), samples.end(), 0.0f);
        }
    }
}
