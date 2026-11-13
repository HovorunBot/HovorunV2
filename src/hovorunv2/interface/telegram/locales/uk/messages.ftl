# Ukrainian translations for HovorunV2 Telegram interactions

# Common
welcome = Ласкаво просимо до Говоруна!
greeting = Привіт, { $name }!
item-count = { $count ->
    [one] { $count } елемент
    [few] { $count } елементи
   *[many] { $count } елементів
}
status-html = <b>Статус:</b> { $status }
plural-files-html = { $count ->
    [one] <b>{ $count }</b> файл оброблено
    [few] <b>{ $count }</b> файли оброблено
   *[many] <b>{ $count }</b> файлів оброблено
}

# Settings
settings-title = ⚙️ <b>Налаштування</b>
settings-language-btn = 🌐 Мова: { $language }
settings-language-choose = Оберіть мову інтерфейсу:
settings-ui-lang-title =
    🌐 <b>Мова інтерфейсу</b>

    Оберіть мову інтерфейсу для взаємодії з ботом:
settings-ui-group-lang-title =
    🌐 <b>Мова інтерфейсу групи</b>

    Оберіть мову інтерфейсу для цієї групи:
settings-lang-menu-title =
    🌐 <b>Налаштування мови</b>

    Налаштуйте мову інтерфейсу, мову перекладу та ігноровані мови:
settings-back-btn = 🔙 Назад
settings-language-btn-uk = 🇺🇦 Українська
settings-language-btn-en = 🇬🇧 English
settings-language-changed = Мову успішно змінено на українську.
settings-group-language-changed = Мову групи успішно змінено на українську.

# Commands
start-description = Почати роботу з ботом
help-description = Переглянути довідку та доступні функції
settings-description = Відкрити меню налаштувань

# Help & Start
help-full-text =
    <b>🦉 HovorunV2 — Ваш медіа-помічник у Telegram</b>

    { $description }

    Покращуйте спілкування у групах завдяки розумним утилітам, динамічному перекладу та зручній обробці медіа.

    <b>🛡 Доступ та авторизація</b>
    Бот працює лише у схвалених чатах. Адміністратори груп можуть запросити доступ:
    • /allow_chat — Запросити схвалення чату у власників бота.

    <b>💬 Підтримка та відгуки</b>
    • /feedback &lt;текст&gt; — Надіслати відгук безпосередньо розробникам бота.

    <b>⚙️ Налаштування (тільки для адмінів груп)</b>
    • /settings — Інтерактивне меню для налаштування модулів та мов.
    • <i>Вікна налаштувань закриваються через 30 секунд бездіяльності.</i>

    <b>Посилання на проєкт:</b>
    • <a href='{ $github_url }'>Репозиторій GitHub</a>
    • <a href='{ $issue_tracker_url }'>Трекер завдань</a>

    <b>Контакти:</b>
    • Email: { $contact_email }

    <i>Наразі я перебуваю на стадії <b>Alpha</b>.</i>
help-available-features = Доступні функції

# Unauthorized Welcome Prompt
unauthorized-prompt-text =
    👋 <b>Привіт! Я Говорун — Ваш медіа-помічник</b>

    Я покращую спілкування у групах завдяки швидкій обробці медіа та автоматичному перекладу.

    ⚠️ <b>Потрібна авторизація чату:</b>
    Для забезпечення надійності я працюю лише у схвалених чатах.
    Довірений користувач може активувати бота кнопкою нижче або командою <code>/allow_chat</code>. Інші адміністратори можуть надіслати запит на схвалення.

    💬 <i>Потрібна допомога чи маєте запитання? Напишіть <code>/feedback &lt;повідомлення&gt;</code> для зв'язку з розробниками.</i>
unauthorized-btn-text = 🛡️ Активувати / Запросити доступ

# Feedback
feedback-instruction =
    💬 <b>Як надіслати відгук:</b>

    Будь ласка, вкажіть ваше повідомлення після <code>/feedback</code>:
    <code>/feedback Ваша пропозиція, повідомлення про помилку чи запитання</code>
feedback-delivered = ✅ <b>Дякуємо!</b> Ваш відгук надіслано безпосередньо команді розробників.
feedback-unreachable = ⚠️ <b>Зверніть увагу:</b> Наразі розробники недоступні. Будь ласка, спробуйте пізніше або зверніться через GitHub.
feedback-unavailable = Відгук недоступний для цього відправника.
feedback-cooldown = Будь ласка, зачекайте хвилинку перед відправкою нового відгуку.

# Access & Permissions
access-approved = ✅ Схвалення групи збережено. Обробка залежить від поточних перевірок доступу.
access-already-approved = ℹ️ Цей чат уже схвалено.
access-requested = ⏳ Запит на доступ збережено для розгляду адміністратором бота.
access-already-pending = ⏳ Запит на схвалення вже очікує на розгляд.
access-rejected = ❌ Запит на доступ відхилено.
access-denied = ❌ Довірені користувачі можуть активувати бота. Інші адміністратори групи можуть надіслати запит на схвалення.
access-unavailable = ❌ Група або користувач недоступні. Виконайте /allow_chat у групі для оновлення.
access-group-banned = ❌ Ця група обмежена. Схвалення не може зняти це обмеження.
access-stale-request = ❌ Цей запит більше не доступний.
access-run-in-group = Виконайте /allow_chat у групі, яку потрібно активувати.
access-private-required = Адміністрування бота можливе лише в приватному чаті.
access-btn-approve = ✅ Схвалити
access-btn-reject = ❌ Відхилити

# Administration
admin-help =
    Використання: /admin ACTION user|group ID|@known_username [options].
    Дії: inspect, trust, untrust, ban, unban, requests, approve, accept, reject, beta-grant, beta-revoke, limits, limit-set, limit-reset.
    Опції: --reason "внутрішня причина", --public "публічне пояснення", --ban-kind personal|full, --feature KEY|*, --request-id ID.
    Опції лімітів: --bucket feature:KEY|family:NAME, --unit requests|resource_units|tokens, --window-seconds N, --limit N, --burst N, --concurrency N, --pending N.
    Внутрішні причини залишаються приватними. Лише --public придатний для розголошення. Зміна ролей потребує CLI.
admin-unavailable = Адміністрування недоступне. Повторіть /admin у приватному чаті.
admin-btn-confirm = Підтвердити
admin-btn-cancel = Скасувати

# Beta Prompts
beta-unavailable = Бета-активація недоступна.
beta-features-hint = Використовуйте /settings для вибору функцій.
beta-queued = { $count ->
    [one] Додано до черги { $count } отримувача бета-ознайомлення.
    [few] Додано до черги { $count } отримувачів бета-ознайомлення.
    [many] Додано до черги { $count } отримувачів бета-ознайомлення.
   *[other] Додано до черги { $count } отримувача бета-ознайомлення.
}
beta-activated = Активовано
beta-declined = Відхилено

# Emergency & Recovery
emergency-help =
    Використання: /emergency FEATURE disable|enable --reason "внутрішня причина" [--public "текст повідомлення"].
    Внутрішні причини залишаються приватними. --public призначений для користувачів. Черга очищується обмеженим відновленням; часткове очищення не повідомляється як завершене.
emergency-unavailable = Аварійне керування недоступне. Використовуйте /emergency у приватному чаті.
recovery-help = Використовуйте /recover user|group ID у приватному чаті для перегляду видаленого об'єкта.
recovery-unavailable = Перегляд відновлення недоступний. Використовуйте /recover у приватному чаті.

# Settings Alerts
settings-unavailable = Налаштування недоступні. Відкрийте /settings знову.
settings-expired = Сесія налаштувань застаріла. Відкрийте /settings знову.
settings-group-usage-unavailable = Статистика групи недоступна.
settings-personal-usage-private-only = Особиста статистика доступна лише в приватних налаштуваннях.
settings-notifications-unavailable = Сповіщення недоступні.
settings-language-not-supported = Мова не підтримується.
settings-feature-unavailable = Функція недоступна.
settings-feature-enabled = { $command } увімкнено
settings-feature-disabled = { $command } вимкнено
settings-target-set = Мову перекладу змінено на { $language }
settings-ignored-toggled = Перемкнуто { $language }
settings-invalid-speech-mode = Некоректний режим мовлення.
settings-speech-unavailable = Налаштування мовлення недоступні.
settings-speech-mode-set = Режим мовлення змінено на: { $mode }
