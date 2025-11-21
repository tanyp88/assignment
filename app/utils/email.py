#import logging 
from flask import render_template
from flask_mail import Message
#logging.basicConfig(level=logging.INFO)
#logger = logging.getLogger(__name__)
from app import mail

def send_email(to, subject="SwiftCheck 通知", logger=None, template=None, **kwargs):
    
    """
    在 Celery 任务里用的发邮件函数
    完全不需要 current_app，靠全局 mail 实例就够了
    """
    msg = Message(
        subject=subject,
        recipients=[to],
        sender=("SwiftCheck", "no-replyp@cctan.ca")  # 写死或从 config 取
    )

    # 纯文本（必备）
    msg.body = kwargs.get('text') or "这是一封来自 SwiftCheck 的系统通知。"

    # HTML 模板（推荐）
    if template:
        msg.html = render_template(template, **kwargs)

    try:
        # 核弹级修复：每次都新建连接，永别 fork 黑洞！
        with mail.connect() as conn:
            mail.send(msg)
        if logger:
            logger.info(f"邮件发送成功 → {to} | 主题: {subject}")
        return True
    except Exception as e:
        if logger:
            logger.error(f"邮件发送失败 → {to} | 错误: {e}", exc_info=True)
        return False