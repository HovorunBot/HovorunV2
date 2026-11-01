## 0.3.2

🛠️ **Hovorun v0.3.2: More Reliable Media Downloads**

* 🔗 **Reliable Link Processing:** Fixed an issue that could leave your media links unanswered.
* 📝 **Useful Video Fallbacks:** When video extraction fails, the bot preserves available post text and provides the original link.
* 💬 **Clearer Request Updates:** The bot now lets you know when your request is queued, cannot run within current limits, or encounters a processing problem.

## 0.3.1

🛠️ **Hovorun v0.3.1: Settings & Update Notification Fixes**

* 🔔 **Notification Settings:** Fixed notification preference buttons that exceeded Telegram's callback limits.
* 📊 **Clearer Usage Display:** Improved quota formatting, time windows, and progress indicators in settings.
* 📬 **Release Updates:** Added concise release notes so update announcements fit in one Telegram message.

## 0.3.0

✨ **New in Hovorun v0.3.0: Group Management, Usage Controls & Notification Preferences!**

* ⚙️ **Private Group Settings:** Manage your groups from a private chat with the bot, including media features, access, and translation settings.
* 🔐 **Explicit Access & Beta Features:** Administrators can manage trusted access and grant beta features to users or entire groups.
* 📊 **Usage & Limits:** View feature usage and allowances. Queued requests now report delays and expiration.
* 🔔 **Notification Preferences:** Choose which release, beta, outage, and announcement notifications you receive.
* 📣 **Announcements:** Administrators can prepare broadcasts, with confirmation before urgent announcements bypass preferences.
* 🛡️ **Administration & Recovery:** Inspect administrative changes and recover deleted records through explicit recovery choices.

## 0.2.1

✨ **New in Hovorun v0.2.1: Feedback, Periodic Reminders & Direct Release Links!**

* 📬 **/feedback Command:** Send suggestions, bugs, or notes directly to the bot developer from any chat.
* ⏰ **Chat Activity Reminders:** Automatic periodic friendly reminders for group chats.
* 🔗 **Direct Release Notes:** Update notifications now link directly to GitHub releases.
* 📺 **Smarter YouTube Extraction:** Improved handling for YouTube Community posts and video links.

## 0.2.0

✨ **New in Hovorun v0.2.0: Reddit, YouTube & Granular Media Controls!**

* 🤖 **Reddit & YouTube Support:** Automatically extracts posts, image galleries, and videos from Reddit as well as YouTube videos, Shorts, and Community posts.
* 🎛️ **Unified Settings & Split Media Toggles:** Use `/settings` to independently toggle video vs. image/text post capture for every supported platform and easily configure translation languages.
* ⚡ **Faster Meta & Reel Extraction:** Rebuilt media pipeline prioritizing `yt-dlp` and multi-stage fallbacks for smoother Instagram, Facebook, and Threads previews.
* 🔐 **Per-Chat Encryption:** Upgraded temporary message caching with unique per-chat encryption keys.

## 0.1.1

🔒 **New in Hovorun v0.1.1: Upgraded Privacy!**

* 🛡️ **Encrypted Messages:** Your privacy matters. While the bot still temporarily caches chat messages for 24 hours to function, all of that data is now fully encrypted.

## 0.1.0

🚀 **Welcome to Hovorun v0.1.0!**

* 📹 **Rich Media:** Automatically extracts and displays media from Twitter, Instagram, TikTok, Threads, Bluesky, and Facebook links.
* 🌍 **Auto-Translation:** Smart language detection and translation to keep chats flowing.
* 🔔 **Update Alerts:** You'll automatically get a heads-up here whenever new features drop.
* 🔑 **Group Access**: Want to use the bot in your own group? Just send allow_chat to request permission and join the queue.
