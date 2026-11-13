# English translations for HovorunV2 Telegram interactions

# Common
welcome = Welcome to Hovorun!
greeting = Hello, { $name }!
item-count = { $count ->
    [one] { $count } item
   *[other] { $count } items
}
status-html = <b>Status:</b> { $status }
plural-files-html = { $count ->
    [one] <b>{ $count }</b> file processed
   *[other] <b>{ $count }</b> files processed
}

# Settings
settings-title = ⚙️ <b>Settings</b>
settings-language-btn = 🌐 Language: { $language }
settings-language-choose = Choose interface language:
settings-ui-lang-title =
    🌐 <b>Personal Interface Language</b>

    Choose the interface language for your interactions:
settings-ui-group-lang-title =
    🌐 <b>Group Interface Language</b>

    Choose the interface language for this group:
settings-lang-menu-title =
    🌐 <b>Language Settings</b>

    Configure interface language, translation target, and ignored languages:
settings-back-btn = 🔙 Back
settings-language-btn-uk = 🇺🇦 Українська
settings-language-btn-en = 🇬🇧 English
settings-language-changed = Language successfully changed to English.
settings-group-language-changed = Group language successfully changed to English.

# Commands
start-description = Start interacting with the bot
help-description = View help and available features
settings-description = Open configuration menu

# Help & Start
help-full-text =
    <b>🦉 HovorunV2 — Your Telegram Media Companion</b>

    { $description }

    I level up your group conversations with smart utilities, dynamic translations, and seamless media integrations.

    <b>🛡 Access Approval</b>
    Bot only works in approved chats. Group admins can request access:
    • /allow_chat — Request bot approval from owners.

    <b>💬 Support & Feedback</b>
    • /feedback &lt;text&gt; — Send feedback directly to bot developers.

    <b>⚙️ Configuration (Group Admins only)</b>
    • /settings — Interactive menu for Modules and Languages.
    • <i>Configuration windows self-destruct after 30s of inactivity.</i>

    <b>Project Links:</b>
    • <a href='{ $github_url }'>GitHub Repository</a>
    • <a href='{ $issue_tracker_url }'>Issue Tracker</a>

    <b>Contact:</b>
    • Email: { $contact_email }

    <i>I am currently in <b>Alpha</b> stage.</i>
help-available-features = Available Features

# Unauthorized Welcome Prompt
unauthorized-prompt-text =
    👋 <b>Hello! I am Hovorun — Your Media Companion</b>

    I enhance group conversations with fast media processing and automatic translation.

    ⚠️ <b>Chat Authorization Required:</b>
    To keep performance reliable, I only operate in approved chats.
    A trusted user can activate the bot with the button below or <code>/allow_chat</code>. Other group administrators can request approval.

    💬 <i>Need help or have questions? Type <code>/feedback &lt;message&gt;</code> to contact developers.</i>
unauthorized-btn-text = 🛡️ Activate / Request Access

# Feedback
feedback-instruction =
    💬 <b>How to send feedback:</b>

    Please provide your message after <code>/feedback</code>:
    <code>/feedback Your suggestion, bug report, or question here</code>
feedback-delivered = ✅ <b>Thank you!</b> Your feedback has been sent directly to the development team.
feedback-unreachable = ⚠️ <b>Notice:</b> Developers are currently unreachable. Please try again later or reach out via GitHub.
feedback-unavailable = Feedback is unavailable for this sender.
feedback-cooldown = Please wait a moment before sending more feedback.
