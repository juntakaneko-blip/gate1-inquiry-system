import os
import sqlite3
from datetime import datetime
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'gate1-local-demo-change-me')
DB = os.path.join(os.path.dirname(__file__), 'gate1.sqlite3')
STATUS = {'NEW': '未対応', 'IN_PROGRESS': '対応中', 'PENDING': '保留', 'DONE': '完了'}
TRANSITIONS = {'NEW': ['IN_PROGRESS'], 'IN_PROGRESS': ['PENDING', 'DONE'], 'PENDING': ['IN_PROGRESS'], 'DONE': ['IN_PROGRESS']}


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')


def connection():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db():
    with connection() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name VARCHAR(50) NOT NULL, role VARCHAR(20) NOT NULL DEFAULT 'MEMBER', is_active BOOLEAN NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS inquiries (id INTEGER PRIMARY KEY, title VARCHAR(100) NOT NULL, body TEXT NOT NULL, requester_name VARCHAR(50) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'NEW', assignee_id INTEGER REFERENCES users(id), priority VARCHAR(10) NOT NULL DEFAULT 'MIDDLE', created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, closed_at DATETIME);
        CREATE TABLE IF NOT EXISTS inquiry_histories (id INTEGER PRIMARY KEY, inquiry_id INTEGER NOT NULL REFERENCES inquiries(id), changed_by INTEGER NOT NULL REFERENCES users(id), field_name VARCHAR(20) NOT NULL, old_value VARCHAR(50), new_value VARCHAR(50), changed_at DATETIME NOT NULL);
        ''')
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
            db.executemany('INSERT INTO users(id,name,role,is_active) VALUES (?,?,?,?)', [(1,'佐藤 花子','ADMIN',1),(2,'鈴木 一郎','MEMBER',1),(3,'田中 美咲','MEMBER',1),(4,'山本 次郎','MEMBER',0)])
        if db.execute('SELECT COUNT(*) FROM inquiries').fetchone()[0] == 0:
            stamp = now()
            db.execute('INSERT INTO inquiries(title,body,requester_name,status,priority,created_at,updated_at) VALUES (?,?,?,?,?,?,?)', ('プリンタが印刷できません','3階の複合機で印刷しようとするとエラーになります。','山田 太郎','NEW','MIDDLE',stamp,stamp))


def current_user(db):
    # Demonstration login only. Never use this role selector as production authentication.
    uid = session.get('user_id', 1)
    return db.execute('SELECT * FROM users WHERE id=? AND is_active=1', (uid,)).fetchone()


def get_inquiry(db, inquiry_id):
    return db.execute('SELECT * FROM inquiries WHERE id=?', (inquiry_id,)).fetchone()


def missing():
    flash('指定された問い合わせは存在しません。', 'error')
    return redirect(url_for('index'))


def stale():
    flash('他のユーザーが更新しました。画面を再読み込みしてください。', 'error')


@app.route('/')
def index():
    with connection() as db:
        inquiries = db.execute('SELECT * FROM inquiries ORDER BY id').fetchall()
    return render_template('index.html', inquiries=inquiries, status_names=STATUS)


@app.post('/demo-user')
def demo_user():
    with connection() as db:
        user = db.execute('SELECT id FROM users WHERE id=? AND is_active=1', (request.form.get('user_id'),)).fetchone()
    if user:
        session['user_id'] = user['id']
    return redirect(request.referrer or url_for('index'))


@app.get('/inquiries/<int:inquiry_id>')
def detail(inquiry_id):
    with connection() as db:
        inquiry = get_inquiry(db, inquiry_id)
        if inquiry is None:
            return missing()
        user = current_user(db)
        users = db.execute('SELECT * FROM users WHERE is_active=1 ORDER BY name COLLATE NOCASE').fetchall()
        assignee = db.execute('SELECT name FROM users WHERE id=?', (inquiry['assignee_id'],)).fetchone() if inquiry['assignee_id'] else None
        histories = db.execute('''SELECT h.*, u.name AS changed_by_name FROM inquiry_histories h JOIN users u ON u.id=h.changed_by WHERE inquiry_id=? ORDER BY changed_at DESC, h.id DESC LIMIT 20''', (inquiry_id,)).fetchall()
        names = {u['id']: u['name'] for u in db.execute('SELECT id,name FROM users')}
    can_assign = inquiry['status'] != 'DONE' and (user['role'] == 'ADMIN' or inquiry['assignee_id'] is None)
    return render_template('detail.html', inquiry=inquiry, user=user, users=users, assignee=assignee, histories=histories, names=names, status_names=STATUS, transitions=TRANSITIONS[inquiry['status']], can_assign=can_assign)


@app.post('/inquiries/<int:inquiry_id>/status')
def change_status(inquiry_id):
    with connection() as db:
        try:
            db.execute('BEGIN IMMEDIATE')
            inquiry = get_inquiry(db, inquiry_id)
            if inquiry is None:
                return missing()
            user = current_user(db)
            target = request.form.get('status', '')
            comment = request.form.get('comment', '')
            if len(comment) > 200:
                flash('コメントは200文字以内で入力してください。', 'error')
            elif target not in TRANSITIONS[inquiry['status']]:
                flash('このステータスへは変更できません。画面を再読み込みしてください。', 'error')
            elif request.form.get('updated_at') != inquiry['updated_at']:
                stale()
            else:
                stamp = now()
                closed_at = stamp if target == 'DONE' else (None if inquiry['status'] == 'DONE' else inquiry['closed_at'])
                assignee_id = inquiry['assignee_id']
                if inquiry['status'] == 'NEW' and target == 'IN_PROGRESS' and assignee_id is None:
                    assignee_id = user['id']
                db.execute('UPDATE inquiries SET status=?,assignee_id=?,closed_at=?,updated_at=? WHERE id=?', (target, assignee_id, closed_at, stamp, inquiry_id))
                db.execute('INSERT INTO inquiry_histories(inquiry_id,changed_by,field_name,old_value,new_value,changed_at) VALUES (?,?,?,?,?,?)', (inquiry_id,user['id'],'status',inquiry['status'],target,stamp))
                if assignee_id != inquiry['assignee_id']:
                    db.execute('INSERT INTO inquiry_histories(inquiry_id,changed_by,field_name,old_value,new_value,changed_at) VALUES (?,?,?,?,?,?)', (inquiry_id,user['id'],'assignee',str(inquiry['assignee_id']) if inquiry['assignee_id'] else None,str(assignee_id),stamp))
                db.commit()
                flash(f'ステータスを「{STATUS[target]}」に変更しました。', 'success')
        except sqlite3.Error:
            db.rollback()
            flash('更新に失敗しました。', 'error')
    return redirect(url_for('detail', inquiry_id=inquiry_id))


@app.post('/inquiries/<int:inquiry_id>/assignee')
def change_assignee(inquiry_id):
    with connection() as db:
        try:
            db.execute('BEGIN IMMEDIATE')
            inquiry = get_inquiry(db, inquiry_id)
            if inquiry is None:
                return missing()
            user = current_user(db)
            raw = request.form.get('assignee_id', '')
            target = int(raw) if raw.isdigit() else None
            target_user = db.execute('SELECT * FROM users WHERE id=? AND is_active=1', (target,)).fetchone() if target is not None else None
            if inquiry['status'] == 'DONE':
                flash('完了済みの問い合わせは担当者を変更できません。', 'error')
            elif user['role'] == 'MEMBER' and not (inquiry['assignee_id'] is None and target == user['id']):
                flash('この操作を行う権限がありません。', 'error')
            elif target is None and not (user['role'] == 'ADMIN' and inquiry['status'] == 'NEW'):
                flash('この操作を行う権限がありません。', 'error')
            elif target is not None and target_user is None:
                flash('指定されたユーザーは選択できません。', 'error')
            elif request.form.get('updated_at') != inquiry['updated_at']:
                stale()
            elif target == inquiry['assignee_id']:
                flash('担当者に変更はありません。', 'error')
            else:
                stamp = now()
                db.execute('UPDATE inquiries SET assignee_id=?,updated_at=? WHERE id=?', (target,stamp,inquiry_id))
                db.execute('INSERT INTO inquiry_histories(inquiry_id,changed_by,field_name,old_value,new_value,changed_at) VALUES (?,?,?,?,?,?)', (inquiry_id,user['id'],'assignee',str(inquiry['assignee_id']) if inquiry['assignee_id'] else None,str(target) if target else None,stamp))
                db.commit()
                flash(f'担当者を「{target_user["name"]}」に変更しました。' if target_user else '担当者を未割り当てに戻しました。', 'success')
        except sqlite3.Error:
            db.rollback()
            flash('更新に失敗しました。', 'error')
    return redirect(url_for('detail', inquiry_id=inquiry_id))


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
