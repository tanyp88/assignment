from app import db, create_app
from app.models import User, Class, Assignment
from werkzeug.security import generate_password_hash
from datetime import datetime

app = create_app()
with app.app_context():
    # Create a teacher
    teacher = User(
        username='teacher1',
        email='teacher1@example.com',
        password_hash=generate_password_hash('teacherpass'),
        role='teacher',
        student_id='TEA_teacher1_1'
    )
    db.session.add(teacher)
    db.session.commit()

    # Create classes
    classes = [
        Class(name='周一11-12(研)结构程序设计(01)致理楼L1-403', teacher_id=teacher.id),
        Class(name='周二01-02(研)结构程序设计(01)致理楼L1-703', teacher_id=teacher.id),
        Class(name='周二03-04(本)数据结构与算法(02)致理楼L3-311', teacher_id=teacher.id),
        Class(name='周二09-10(研)桥梁施工与监测(01)致理楼L1-203', teacher_id=teacher.id),
        Class(name='周四07-08(本)桥梁工程概论(01)致理楼L3-518', teacher_id=teacher.id)
    ]
    db.session.add_all(classes)
    db.session.commit()

    # Create an assignment
    assignment = Assignment(
        title='Math Assignment 1',
        description='Solve problems x+y=2,x-y=4',
        class_id=classes[0].id,
        due_date=datetime.now()
    )
    db.session.add(assignment)
    db.session.commit()

    print("Database seeded successfully!")