# analyze_fake_checkins.py
# 2025 年最优雅的代签屠刀

from collections import defaultdict
from app import create_app, db
from app.models import Attendance, User
from datetime import date

app = create_app()
app.app_context().push()

today = date.today()

# 1. 统计每个指纹今天签到了哪些人
fp_to_students = defaultdict(set)
fp_to_count = defaultdict(int)

atts = Attendance.query.filter(db.func.date(Attendance.checkin_time) == today).all()

for att in atts:
    if att.fingerprint:
        fp_to_students[att.fingerprint].add(att.student_id)
        fp_to_count[att.fingerprint] += 1

# 2. 找出“一指纹多人”的记录（铁证代签）
print("今日代签铁证（一设备多人签到）".center(60, "="))
for fp, students in fp_to_students.items():
    if len(students) > 1:
        print(f"\n指纹: {fp[:16]}...")
        for sid in students:
            user = User.query.filter_by(student_id=sid).first()
            name = user.name if user else "未知"
            print(f"  → {sid} {name}")
        print(f"  共 {len(students)} 人使用同一设备签到")

# 3. 额外彩蛋：指纹异常频繁（一天签 10+ 次）
print("\n高频设备（可能被借用）".center(60, "="))
for fp, count in fp_to_count.items():
    if count >= 8:
        students = fp_to_students[fp]
        print(f"指纹 {fp[:16]}... 今天签到 {count} 次，涉及 {len(students)} 人")