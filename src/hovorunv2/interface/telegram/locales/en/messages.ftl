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

# Access & Permissions
access-approved = ✅ Group approval saved. Processing remains subject to current access checks.
access-already-approved = ℹ️ This chat is already approved.
access-requested = ⏳ Access request saved for bot administrator review.
access-already-pending = ⏳ Approval is already pending.
access-rejected = ❌ Access request rejected.
access-denied = ❌ Trusted users can activate the bot. Other group admins can request approval.
access-unavailable = ❌ Group or user unavailable. Run /allow_chat in the group to refresh.
access-group-banned = ❌ This group is restricted. Approval cannot remove that restriction.
access-stale-request = ❌ This request is no longer available.
access-run-in-group = Run /allow_chat in the group you want to activate.
access-private-required = Bot administration requires a private chat.
access-btn-approve = ✅ Approve
access-btn-reject = ❌ Reject

# Administration
admin-help =
    Use /admin ACTION user|group ID|@known_username [options].
    Actions: inspect, trust, untrust, ban, unban, requests, approve, accept, reject, beta-grant, beta-revoke, limits, limit-set, limit-reset.
    Options: --reason "internal reason", --public "safe explanation", --ban-kind personal|full, --feature KEY|*, --request-id ID.
    Limit options: --bucket feature:KEY|family:NAME, --unit requests|resource_units|tokens, --window-seconds N, --limit N, --burst N, --concurrency N, --pending N.
    Internal reasons stay private. Only --public is suitable for disclosure. Role changes require CLI.
admin-unavailable = Administration unavailable. Repeat /admin privately.
admin-btn-confirm = Confirm
admin-btn-cancel = Cancel

# Beta Prompts
beta-unavailable = Beta activation unavailable.
beta-features-hint = Use /settings to choose features.
beta-queued = { $count ->
    [one] Queued { $count } beta introduction recipient.
   *[other] Queued { $count } beta introduction recipients.
}
beta-activated = Activated
beta-declined = Declined

# Emergency & Recovery
emergency-help =
    Use /emergency FEATURE disable|enable --reason "internal reason" [--public "outage text"].
    Internal reasons stay private. --public is suitable for affected users. Queued work is cleared by the bounded recovery sweep; partial purges are never reported as complete.
emergency-unavailable = Emergency controls unavailable. Use /emergency privately.
recovery-help = Use /recover user|group ID privately to inspect a deleted identity.
recovery-unavailable = Recovery inspection unavailable. Use /recover privately.

# Settings Alerts
settings-unavailable = Settings unavailable. Reopen /settings.
settings-expired = Settings expired. Reopen /settings.
settings-group-usage-unavailable = Group usage unavailable.
settings-personal-usage-private-only = Personal usage is available in private settings.
settings-notifications-unavailable = Notifications unavailable.
settings-language-not-supported = Language not supported.
settings-feature-unavailable = Feature unavailable.
settings-feature-enabled = { $command } enabled
settings-feature-disabled = { $command } disabled
settings-target-set = Target set to { $language }
settings-ignored-toggled = Toggled { $language }
settings-invalid-speech-mode = Invalid speech mode.
settings-speech-unavailable = Speech configuration unavailable.
settings-speech-mode-set = Speech mode set to: { $mode }

# Translation UI
translation-label = Translated:
translation-btn-translate = Translate
translation-btn-continue = Continue translation
translation-progress = Translating...
translation-help = Reply to a message with /translate [target] to translate the replied text or selected quote.
translation-reply-required = Reply to a message with text or caption to translate it.
translation-empty-source = No translatable text found in the selected message or quote.
translation-invalid-target = Unsupported target language: { $language }.
translation-unavailable = Translation is temporarily unavailable. Please try again later.
translation-expired = Translation snapshot has expired. Request a new translation.
translation-denied = You do not have access to use translation in this chat.
translation-already-complete = Translation is already complete.
translation-same-language = Source text is already in { $language }.
translation-remaining-parts = { $count ->
    [one] { $count } more part available
   *[other] { $count } more parts available
}
