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
settings-main-menu-text =
    ⚙️ <b>Hovorun Settings</b>

    Select a category to configure:
settings-main-group-summary =
    Group: <code>{ $group_id }</code>
    UI: <code>{ $ui_locale }</code> | Target: <code>{ $target_lang }</code> | Ignored: <code>{ $ignored }</code> | Speech: <code>{ $speech }</code>
settings-none = none
settings-speech-mode-off = Off
settings-speech-mode-manual_only = Manual only
settings-speech-mode-manual_and_automatic = Manual + automatic
settings-btn-languages = 🌐 Languages
settings-btn-modules = ⚙️ Modules
settings-btn-speech = 🗣 Speech recognition
settings-btn-access = 🔎 Access & permissions
settings-btn-my-usage = 📊 My usage
settings-btn-group-usage = 📊 Group usage
settings-btn-notifications = 🔔 Notifications
settings-btn-close = ❌ Close
settings-btn-other-groups = 👥 Other groups

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
    To select a person without looking up their ID: /admin trust user.
    Actions: inspect, trust, untrust, ban, unban, requests, approve, accept, reject, beta-grant, beta-revoke, limits, limit-set, limit-reset.
    Options: --reason "internal reason", --public "safe explanation", --ban-kind personal|full, --feature KEY|*, --request-id ID.
    Limit options: --bucket feature:KEY|family:NAME, --unit requests|resource_units|tokens, --window-seconds N, --limit N, --burst N, --concurrency N, --pending N.
    Internal reasons stay private. Only --public is suitable for disclosure. Role changes require CLI.
admin-unavailable = Administration unavailable. Repeat /admin privately.
admin-unknown-handle = This username is not known to the bot. Use the numeric Telegram ID to trust a new user, or ask them to send /start to the bot and retry their username.
admin-choose-user = Choose user
admin-user-selection-prompt = Choose one person in Telegram, then confirm the trust change. The selection expires in 30 seconds. Only the selected person's numeric ID is requested.
admin-user-selected = User selected. Review the target below before confirming trust.
admin-selection-unavailable = Selection expired, was already used, or the user is unavailable. Repeat /admin trust user. Deleted users require explicit recovery.
admin-invalid-request = Invalid administration request or inactive target. Check /admin for syntax. Deleted users require explicit recovery before trust can be granted.
admin-btn-confirm = Confirm
admin-btn-cancel = Cancel
admin-saved = Changes saved.
admin-unchanged = No changes needed.
admin-cancelled = Action cancelled.

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

# Guided Chat Setup
setup-btn-configure = ⚙️ Configure chat
setup-btn-next = Next ➡️
setup-btn-back = ⬅️ Back
setup-btn-cancel = ✖️ Cancel
setup-btn-apply = ✅ Apply
setup-btn-clear-ignored = Clear excluded (translate all)
setup-step-ui-title = 🛠 Chat setup (1/5): Bot interface language for this chat
setup-step-ui-body = Selected: { $ui_locale }. Changing UI locale does not alter translation target language.
setup-step-target-title = 🛠 Chat setup (2/5): Translation target language
setup-step-target-body = Selected target language: { $target_lang }.
setup-step-ignored-title = 🛠 Chat setup (3/5): Languages excluded from auto-translation
setup-step-ignored-body = Excluded languages: { $ignored }.
setup-step-features-title = 🛠 Chat setup (4/5): Enabled features
setup-step-features-body = Choose active features for this chat.
setup-step-speech-title = 🛠 Chat setup (5/5): Speech recognition behavior
setup-step-speech-body = Selected mode: { $speech_mode }.
setup-step-review-title = 🛠 Review before applying
setup-step-review-body =
    Interface language: { $ui_locale }
    Target language: { $target_lang }
    Excluded languages: { $ignored }
    Enabled features: { $features }
    Speech mode: { $speech_mode }
setup-applied-summary =
    ✅ Chat configuration applied:
    Interface language: { $ui_locale }
    Target language: { $target_lang }
    Excluded languages: { $ignored }
    Enabled features: { $features }
    Speech mode: { $speech_mode }
setup-cancelled = ✖️ Chat setup cancelled. Saved configuration was not changed.
setup-expired = ⏱ Setup session expired. Run /setup again.
setup-denied = ⛔ Only the administrator who started this setup session can control it.
setup-in-progress = ⏳ Another administrator already has an active setup session in this chat.
setup-conflict = ⚠️ Chat settings changed concurrently. Review the refreshed state and confirm again.
setup-unavailable = ⚠️ One or more selected features are currently unavailable.
setup-group-only = ℹ️ Guided setup runs directly inside the target group chat.
setup-duplicate = This action was already processed.
setup-none = None
