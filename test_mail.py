# 1. 最快测试（"纯文本）
from app.utils.email import send_email   # 改成你实际的路径
send_email(
    to="tanyp@szu.edu.cn",        # 换成你自己的邮箱，能收到就行
    subject="【测试】Flask-Mail 发信成功！",
    text="如果您看到这封邮件，说明 send_email() 完全正常！\n\n—— 2025年谭也平核弹级测试"
)