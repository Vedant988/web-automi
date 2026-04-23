import sys
import re

with open("groq_chat.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1
select_profile_start = -1
select_profile_end = -1

for i, line in enumerate(lines):
    if line.startswith("BLOCKED_PAGE_SIGNALS = "):
        start_idx = i
    if line.startswith("DEFAULT_FINAL_MODEL = "):
        end_idx = i
    if line.startswith("def select_chrome_profile():"):
        select_profile_start = i
    if line.startswith("def main():"):
        select_profile_end = i

if start_idx == -1 or end_idx == -1 or select_profile_start == -1 or select_profile_end == -1:
    print("Could not find boundaries")
    sys.exit(1)

browser_tools_lines = [
    "import os\n",
    "import re\n",
    "import sys\n",
    "import json\n",
    "import time\n",
    "import asyncio\n",
    "from urllib.parse import urlparse, urljoin\n",
    "from bs4 import BeautifulSoup\n",
    "from playwright.async_api import async_playwright\n",
    "\n",
    "SELECTED_CHROME_PROFILE = None\n",
    "\n"
]

browser_tools_lines.extend(lines[select_profile_start:select_profile_end])
browser_tools_lines.extend(lines[start_idx:end_idx])

# Fix global reference in select_chrome_profile
for i, line in enumerate(browser_tools_lines):
    if "global SELECTED_CHROME_PROFILE" in line:
        pass # It's valid in the same module now

with open("browser_tools.py", "w", encoding="utf-8") as f:
    f.writelines(browser_tools_lines)

# Now rewrite groq_chat.py
new_groq_chat = lines[:start_idx]
new_groq_chat.append("from browser_tools import search_web, navigate_url, select_chrome_profile\n")
new_groq_chat.append("import browser_tools\n\n")
new_groq_chat.extend(lines[end_idx:select_profile_start])

# Need to fix the global assignment in main()
main_lines = lines[select_profile_end:]
for i, line in enumerate(main_lines):
    if "global SELECTED_CHROME_PROFILE" in line:
        main_lines[i] = ""
    elif "SELECTED_CHROME_PROFILE = select_chrome_profile()" in line:
        main_lines[i] = "    browser_tools.SELECTED_CHROME_PROFILE = select_chrome_profile()\n"

new_groq_chat.extend(main_lines)

with open("groq_chat.py", "w", encoding="utf-8") as f:
    f.writelines(new_groq_chat)

print("Refactoring complete.")
