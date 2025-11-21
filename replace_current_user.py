# replace_current_user.py
import os

for root, dirs, files in os.walk("app/templates"):
    for file in files:
        if file.endswith(".html"):
            path = os.path.join(root, file)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if "current_user" in content:
                new_content = content.replace("current_user", "user")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                print(f"已替换: {path}")