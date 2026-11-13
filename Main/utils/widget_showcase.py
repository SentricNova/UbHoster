"""Rich HTML/CSS Interactive Showcase Widget for Main Hosting Bot (/start)."""

from __future__ import annotations

import html


def build_main_bot_showcase_widget_html(bot_name: str = "AntiGravity Hosting", bot_username: str = "PyroTesting_Bot") -> str:
    safe_name = html.escape(bot_name[:40])
    safe_uname = html.escape(bot_username.lstrip("@")[:32])

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<style>
  :root {{
    --bg: #090d1a;
    --card-bg: rgba(255, 255, 255, 0.05);
    --card-border: rgba(255, 255, 255, 0.12);
    --text: #f1f5f9;
    --text-muted: #94a3b8;
    --accent-cyan: #06b6d4;
    --accent-blue: #3b82f6;
    --accent-purple: #a855f7;
    --accent-pink: #ec4899;
    --accent-green: #10b981;
    --accent-amber: #f59e0b;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: radial-gradient(circle at 10% 10%, rgba(6, 182, 212, 0.18), transparent 45%),
                radial-gradient(circle at 90% 90%, rgba(168, 85, 247, 0.18), transparent 45%),
                var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 12px;
    font-size: 13px;
    line-height: 1.45;
  }}
  .glass-container {{
    background: rgba(15, 23, 42, 0.75);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid var(--card-border);
    border-radius: 18px;
    padding: 14px;
    box-shadow: 0 16px 36px rgba(0, 0, 0, 0.45);
  }}
  .hero {{
    text-align: center;
    padding: 8px 4px 14px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    margin-bottom: 12px;
  }}
  .badge-row {{
    display: flex;
    justify-content: center;
    gap: 6px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }}
  .pill {{
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 3px 8px;
    border-radius: 999px;
    background: rgba(6, 182, 212, 0.15);
    border: 1px solid rgba(6, 182, 212, 0.35);
    color: #67e8f9;
  }}
  .pill.green {{
    background: rgba(16, 185, 129, 0.15);
    border-color: rgba(16, 185, 129, 0.35);
    color: #6ee7b7;
  }}
  .pill.purple {{
    background: rgba(168, 85, 247, 0.15);
    border-color: rgba(168, 85, 247, 0.35);
    color: #d8b4fe;
  }}
  .hero-title {{
    font-size: 19px;
    font-weight: 900;
    letter-spacing: -0.3px;
    background: linear-gradient(135deg, #38bdf8, #818cf8, #c084fc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 4px;
  }}
  .hero-desc {{
    font-size: 11.5px;
    color: var(--text-muted);
  }}
  .search-box {{
    width: 100%;
    padding: 8px 12px;
    border-radius: 10px;
    border: 1px solid var(--card-border);
    background: rgba(0, 0, 0, 0.28);
    color: var(--text);
    font-size: 12px;
    margin-bottom: 10px;
    outline: none;
    transition: border-color 0.2s;
  }}
  .search-box:focus {{
    border-color: var(--accent-cyan);
  }}
  .tab-bar {{
    display: flex;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 6px;
    margin-bottom: 10px;
    scrollbar-width: none;
  }}
  .tab-bar::-webkit-scrollbar {{ display: none; }}
  .tab-btn {{
    white-space: nowrap;
    padding: 6px 12px;
    font-size: 11px;
    font-weight: 700;
    border-radius: 999px;
    border: 1px solid var(--card-border);
    background: rgba(255, 255, 255, 0.04);
    color: var(--text-muted);
    cursor: pointer;
    transition: all 0.2s;
  }}
  .tab-btn.active {{
    background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
    color: #ffffff;
    border-color: transparent;
    box-shadow: 0 4px 14px rgba(6, 182, 212, 0.35);
  }}
  .feature-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 310px;
    overflow-y: auto;
    padding-right: 2px;
  }}
  .feature-card {{
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 10px 12px;
    transition: transform 0.15s, border-color 0.15s;
  }}
  .feature-card:hover {{
    border-color: rgba(56, 189, 248, 0.4);
    transform: translateY(-1px);
  }}
  .card-top {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 4px;
  }}
  .card-title {{
    font-size: 13px;
    font-weight: 800;
    color: #f8fafc;
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  .tag {{
    font-size: 9.5px;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.08);
    color: #cbd5e1;
  }}
  .tag.hot {{ background: rgba(236, 72, 153, 0.2); color: #f472b6; }}
  .tag.blue {{ background: rgba(59, 130, 246, 0.2); color: #93c5fd; }}
  .tag.green {{ background: rgba(16, 185, 129, 0.2); color: #6ee7b7; }}
  .card-desc {{
    font-size: 11.5px;
    color: var(--text-muted);
    line-height: 1.4;
  }}
  .card-cmd {{
    margin-top: 6px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 11px;
    color: #38bdf8;
    background: rgba(0, 0, 0, 0.25);
    display: inline-block;
    padding: 2px 7px;
    border-radius: 6px;
    border: 1px solid rgba(56, 189, 248, 0.2);
  }}
  .cta-box {{
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .cta-text {{
    font-size: 11px;
    color: var(--text-muted);
  }}
  .cta-link {{
    font-size: 11px;
    font-weight: 800;
    color: #38bdf8;
    text-decoration: none;
    padding: 4px 10px;
    border-radius: 8px;
    background: rgba(56, 189, 248, 0.12);
    border: 1px solid rgba(56, 189, 248, 0.3);
  }}
</style>
</head>
<body>
<div class="glass-container">
  <div class="hero">
    <div class="badge-row">
      <span class="pill green">🟢 Engine Online</span>
      <span class="pill">⚡ Low-RAM LMDB</span>
      <span class="pill purple">🔒 Safe MTProto</span>
    </div>
    <div class="hero-title">⚡ {safe_name}</div>
    <div class="hero-desc">High-Performance Zero-Crash Telegram Bot &amp; Userbot Hosting Cloud</div>
  </div>

  <input class="search-box" id="q" placeholder="🔍 Search features, commands or tools..." oninput="filterFeatures()">

  <div class="tab-bar">
    <button class="tab-btn active" onclick="selectTab('all', this)">✨ All</button>
    <button class="tab-btn" onclick="selectTab('bot', this)">🤖 Bot Hosting</button>
    <button class="tab-btn" onclick="selectTab('userbot', this)">👤 Userbots</button>
    <button class="tab-btn" onclick="selectTab('moderation', this)">🛡️ Admin</button>
    <button class="tab-btn" onclick="selectTab('tools', this)">⚡ Power Tools</button>
    <button class="tab-btn" onclick="selectTab('affiliate', this)">🎁 Affiliate</button>
  </div>

  <div class="feature-list" id="features"></div>

  <div class="cta-box">
    <span class="cta-text">Ready to deploy? Tap <b>/addbot</b> or <b>/addacc</b></span>
    <a class="cta-link" href="https://t.me/{safe_uname}">Dashboard</a>
  </div>
</div>

<script>
const items = [
  {{
    cat: "bot",
    title: "🤖 Hosted Bots & Custom Welcome",
    tag: "Bot API",
    tagClass: "blue",
    desc: "Host your Telegram Bots via Bot Token. Set custom /start formatting with dynamic variables: {{fullname}}, {{mention}}, {{id}}, {{username}}.",
    cmd: "/addbot & /settings"
  }},
  {{
    cat: "bot",
    title: "📢 Dedicated Logger Groups",
    tag: "Audit",
    tagClass: "green",
    desc: "Route error logs, client notifications, and activity reports to a designated logger group to keep your team informed in real time.",
    cmd: "/settings -> Logger Group"
  }},
  {{
    cat: "userbot",
    title: "👤 500+ Userbots on Bounded LMDB",
    tag: "Low RAM",
    tagClass: "green",
    desc: "Connect user accounts safely through an on-screen interactive keypad. Low RAM footprint (<2.5MB per client) with zero SQLite lock contention.",
    cmd: "/addacc"
  }},
  {{
    cat: "tools",
    title: "📣 Fast Multi-Client Broadcast",
    tag: "High Speed",
    tagClass: "hot",
    desc: "Broadcast messages to users, groups, or channels across all or selected hosted clients with adjustable speeds, scheduling, and auto-delete timers.",
    cmd: "/broadcast"
  }},
  {{
    cat: "moderation",
    title: "🛡️ Group Moderation & Mass Actions",
    tag: "Admin",
    tagClass: "blue",
    desc: "Execute /ban, /unban, /mute, /unmute, /kick, as well as /banall and /kickall with filters (deleted accounts, offline, or active members).",
    cmd: "/ban, /kickall [filter]"
  }},
  {{
    cat: "tools",
    title: "🔄 Smart Forwarder with Range Copy",
    tag: "Utility",
    tagClass: "blue",
    desc: "Transfer files or messages from chat A to B. Automatically extracts start and end message IDs, handles floodwaits, and supports copy vs forward mode.",
    cmd: "/forward [from_chat] [start] [end]"
  }},
  {{
    cat: "tools",
    title: "👥 Targeted Member Tagging",
    tag: "Engagement",
    tagClass: "hot",
    desc: "Mention group members safely: /tagall, /tagadmins, /tagusers, or /tagactive (only members seen recently), batching 5 mentions per message.",
    cmd: "/tagall, /tagactive"
  }},
  {{
    cat: "tools",
    title: "🎯 Selective Group Raids & Defense",
    tag: "Fun & Defense",
    tagClass: "hot",
    desc: "Lock target user IDs in RAM to reply instantly whenever they send a message: /raid, /fraid, /spam, /fspam, and /tspam.",
    cmd: "/raid @user, /fraid, /spam"
  }},
  {{
    cat: "userbot",
    title: "📁 Member Scraper to Saved Messages",
    tag: "Userbot",
    tagClass: "green",
    desc: "Scrape members with usernames into a structured file sent straight to your Saved Messages and logger group, ready for outbound campaigns.",
    cmd: ".scrape [limit]"
  }},
  {{
    cat: "userbot",
    title: "⏰ Timer-Based Media Auto-Saver",
    tag: "Userbot",
    tagClass: "green",
    desc: "Automatically capture and archive expiring self-destructing timer photos and voice notes into your private Saved Messages.",
    cmd: "Automated in background"
  }},
  {{
    cat: "userbot",
    title: "🛑 Anti-Spam Warn Mode",
    tag: "Security",
    tagClass: "blue",
    desc: "Configure automated actions for incoming direct messages: warn spammers with custom warnings, mute, or auto-block if threshold is exceeded.",
    cmd: "/settings -> Anti-Spam"
  }},
  {{
    cat: "affiliate",
    title: "🎁 Affiliate Program & Quota Expansion",
    tag: "Rewards",
    tagClass: "hot",
    desc: "Share your personal invite link. When invited friends join, you unlock premium perks, increased bot hosting slots, and uninterrupted hosting.",
    cmd: "/invite"
  }},
  {{
    cat: "tools",
    title: "🔑 Instant Session String Export",
    tag: "Export",
    tagClass: "blue",
    desc: "Export your hosted account or bot MTProto session string at any time with a single command or button click.",
    cmd: "/export"
  }}
];

let curTab = "all";

function selectTab(cat, btn) {{
  curTab = cat;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  filterFeatures();
}}

function filterFeatures() {{
  const query = (document.getElementById('q').value || "").toLowerCase().trim();
  const out = document.getElementById('features');
  out.innerHTML = "";

  const filtered = items.filter(it => {{
    const matchCat = (curTab === "all" || it.cat === curTab);
    const matchQ = !query || it.title.toLowerCase().includes(query) || it.desc.toLowerCase().includes(query) || it.cmd.toLowerCase().includes(query);
    return matchCat && matchQ;
  }});

  if (!filtered.length) {{
    out.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-muted);font-size:12px;">No matching features found. Try another search!</div>';
    return;
  }}

  filtered.forEach(it => {{
    const el = document.createElement('div');
    el.className = 'feature-card';
    el.innerHTML = `
      <div class="card-top">
        <span class="card-title">${{it.title}}</span>
        <span class="tag ${{it.tagClass}}">${{it.tag}}</span>
      </div>
      <div class="card-desc">${{it.desc}}</div>
      <span class="card-cmd">${{it.cmd}}</span>
    `;
    out.appendChild(el);
  }});
}}

filterFeatures();
</script>
</body>
</html>"""
