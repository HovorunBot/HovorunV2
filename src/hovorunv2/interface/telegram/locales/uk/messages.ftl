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
