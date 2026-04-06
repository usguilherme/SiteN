from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import os, json as jsonlib

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'studytogether-secret-2024-xk9')
database_url = os.environ.get('DATABASE_URL', '')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
if not database_url:
    db_path = os.path.join(os.path.dirname(__file__), 'study.db')
    database_url = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

# ─── MODELS ──────────────────────────────────────────────────────────────────
class User(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    display_name  = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    avatar_color  = db.Column(db.String(20), default='#3b82f6')
    subjects      = db.relationship('Subject', backref='user', lazy=True)
    sessions      = db.relationship('StudySession', backref='user', lazy=True)

class Subject(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    name     = db.Column(db.String(100), nullable=False)
    color    = db.Column(db.String(20), default='#3b82f6')
    emoji    = db.Column(db.String(10), default='📚')
    user_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sessions = db.relationship('StudySession', backref='subject', lazy=True)

class StudySession(db.Model):
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject_id       = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    start_time       = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    end_time         = db.Column(db.DateTime)
    duration_minutes = db.Column(db.Float, default=0)

class ActiveSession(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    subject    = db.relationship('Subject')
    user       = db.relationship('User')

class PushSubscription(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subscription = db.Column(db.Text, nullable=False)
    user         = db.relationship('User')

class StudyGoal(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=True)
    period     = db.Column(db.String(10), default='week')
    minutes    = db.Column(db.Float, default=60)
    user       = db.relationship('User')
    subject    = db.relationship('Subject')

class StudyNote(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey('study_session.id'), nullable=True)
    content    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user       = db.relationship('User')

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def fmt_duration(minutes):
    if not minutes or minutes < 1: return "< 1 min"
    if minutes < 60: return f"{int(minutes)} min"
    h = int(minutes // 60); m = int(minutes % 60)
    return f"{h}h {m:02d}min" if m else f"{h}h"

def get_active_status(user_id):
    active = ActiveSession.query.filter_by(user_id=user_id).first()
    if not active: return None
    elapsed = (datetime.utcnow() - active.start_time).total_seconds() / 60
    return {'subject': active.subject.name, 'subject_color': active.subject.color,
            'subject_emoji': active.subject.emoji, 'elapsed_min': elapsed,
            'elapsed_str': fmt_duration(elapsed), 'start_iso': active.start_time.isoformat()}

def get_streak(user_id):
    sessions = StudySession.query.filter_by(user_id=user_id).order_by(StudySession.start_time.desc()).all()
    if not sessions: return {'current': 0, 'longest': 0}
    study_days = sorted(set(s.start_time.date() for s in sessions), reverse=True)
    today = (datetime.utcnow() - timedelta(hours=3)).date()
    current = 0
    check = today
    for d in study_days:
        if d == check:
            current += 1
            check = d - timedelta(days=1)
        elif d == today - timedelta(days=1) and current == 0:
            current += 1
            check = d - timedelta(days=1)
        else:
            break
    longest = 1; run = 1
    for i in range(1, len(study_days)):
        if (study_days[i-1] - study_days[i]).days == 1:
            run += 1
            if run > longest: longest = run
        else:
            run = 1
    return {'current': current, 'longest': max(longest, current)}

def get_goals_progress(user_id):
    goals = StudyGoal.query.filter_by(user_id=user_id).all()
    today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday())
    result = []
    for g in goals:
        since = datetime.combine(today if g.period == 'day' else week_start, datetime.min.time())
        q = StudySession.query.filter(StudySession.user_id == user_id, StudySession.start_time >= since)
        if g.subject_id:
            q = q.filter(StudySession.subject_id == g.subject_id)
        done = sum(s.duration_minutes or 0 for s in q.all())
        pct = min(100, round((done / g.minutes) * 100)) if g.minutes > 0 else 0
        result.append({
            'id': g.id, 'period': g.period, 'target_min': g.minutes,
            'done_min': done, 'pct': pct,
            'target_str': fmt_duration(g.minutes), 'done_str': fmt_duration(done),
            'subject': g.subject.name if g.subject else 'Total',
            'subject_emoji': g.subject.emoji if g.subject else '📚',
            'subject_color': g.subject.color if g.subject else '#c9a84c',
        })
    return result

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f: return jsonlib.load(f)
    return {}

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f: jsonlib.dump(cfg, f, indent=2)

def get_api_key():
    key = os.environ.get('ANTHROPIC_API_KEY', '')
    if key: return key
    return load_config().get('api_key', '')

def send_push_to_user(user_id, title, body):
    try:
        from pywebpush import webpush, WebPushException
        cfg = load_config()
        vapid_private = os.environ.get('VAPID_PRIVATE_KEY', cfg.get('vapid_private', ''))
        vapid_email   = os.environ.get('VAPID_EMAIL', cfg.get('vapid_email', 'mailto:admin@studytogether.app'))
        if not vapid_private: return
        subs = PushSubscription.query.filter_by(user_id=user_id).all()
        data = jsonlib.dumps({'title': title, 'body': body})
        for sub in subs:
            try:
                sub_info = jsonlib.loads(sub.subscription)
                webpush(subscription_info=sub_info, data=data,
                        vapid_private_key=vapid_private, vapid_claims={'sub': vapid_email})
            except WebPushException as e:
                if e.response and e.response.status_code in [404, 410]:
                    db.session.delete(sub); db.session.commit()
    except Exception:
        pass

# ─── PAGE ROUTES ──────────────────────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            return redirect(url_for('dashboard'))
        error = 'Usuário ou senha incorretos.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    partner = User.query.filter(User.id != user.id).first()
    subjects = Subject.query.filter_by(user_id=user.id).order_by(Subject.name).all()
    return render_template('dashboard.html', user=user, partner=partner, subjects=subjects)

@app.route('/timer')
@login_required
def timer():
    user = User.query.get(session['user_id'])
    subjects = Subject.query.filter_by(user_id=user.id).order_by(Subject.name).all()
    active = ActiveSession.query.filter_by(user_id=user.id).first()
    return render_template('timer.html', user=user, subjects=subjects, active=active)

@app.route('/guia')
@login_required
def guia():
    user = User.query.get(session['user_id'])
    subjects = Subject.query.filter_by(user_id=user.id).order_by(Subject.name).all()
    api_key_set = bool(get_api_key())
    return render_template('guia.html', user=user, subjects=subjects, api_key_set=api_key_set)

# ─── API ──────────────────────────────────────────────────────────────────────
@app.route('/api/status')
@login_required
def api_status():
    result = {}
    for u in User.query.all():
        st = get_active_status(u.id)
        result[str(u.id)] = {'id': u.id, 'display_name': u.display_name,
            'avatar_color': u.avatar_color, 'studying': st is not None, 'status': st}
    return jsonify(result)

@app.route('/api/start-session', methods=['POST'])
@login_required
def start_session():
    data = request.get_json(); user_id = session['user_id']
    ActiveSession.query.filter_by(user_id=user_id).delete()
    active = ActiveSession(user_id=user_id, subject_id=data['subject_id'], start_time=datetime.utcnow())
    db.session.add(active); db.session.commit()
    user    = User.query.get(user_id)
    subject = Subject.query.get(data['subject_id'])
    partner = User.query.filter(User.id != user_id).first()
    if partner:
        send_push_to_user(partner.id, f"{subject.emoji} {user.display_name} começou a estudar!",
                          f"Estudando {subject.name} agora. Bora junto? 📚")
    return jsonify({'success': True, 'start_time': active.start_time.isoformat()})

@app.route('/api/stop-session', methods=['POST'])
@login_required
def stop_session():
    user_id = session['user_id']
    active = ActiveSession.query.filter_by(user_id=user_id).first()
    if not active: return jsonify({'success': False, 'error': 'Nenhuma sessão ativa'})
    end_time = datetime.utcnow()
    duration = (end_time - active.start_time).total_seconds() / 60
    s = StudySession(user_id=user_id, subject_id=active.subject_id,
                     start_time=active.start_time, end_time=end_time, duration_minutes=duration)
    db.session.add(s); db.session.delete(active); db.session.commit()
    user    = User.query.get(user_id)
    partner = User.query.filter(User.id != user_id).first()
    if partner:
        send_push_to_user(partner.id, f"⏹ {user.display_name} encerrou a sessão",
                          f"Estudou por {fmt_duration(duration)}. Ótimo trabalho! ✨")
    return jsonify({'success': True, 'duration_minutes': duration,
                    'duration_str': fmt_duration(duration), 'session_id': s.id})

@app.route('/api/manual-session', methods=['POST'])
@login_required
def manual_session():
    data = request.get_json(); user_id = session['user_id']
    try:
        # Parse local time and convert to UTC (Brasília = UTC-3)
        start_time = datetime.fromisoformat(data['start_time']) + timedelta(hours=3)
        end_time   = datetime.fromisoformat(data['end_time']) + timedelta(hours=3)
        duration   = (end_time - start_time).total_seconds() / 60
        if duration <= 0: return jsonify({'success': False, 'error': 'Duração inválida'})
        s = StudySession(user_id=user_id, subject_id=data['subject_id'],
                         start_time=start_time, end_time=end_time, duration_minutes=duration)
        db.session.add(s); db.session.commit()
        return jsonify({'success': True, 'duration_str': fmt_duration(duration)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/weekly-stats')
@login_required
def weekly_stats():
    user_id = session['user_id']; today = (datetime.utcnow() - timedelta(hours=3)).date()
    week_start = today - timedelta(days=today.weekday()); day_names = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']
    days = {(week_start + timedelta(days=i)).isoformat(): 0.0 for i in range(7)}
    for s in StudySession.query.filter(StudySession.user_id == user_id,
        StudySession.start_time >= datetime.combine(week_start, datetime.min.time())).all():
        key = s.start_time.date().isoformat()
        if key in days: days[key] += s.duration_minutes or 0
    mins = list(days.values()); total = sum(mins)
    since = datetime.utcnow() - timedelta(days=30); agg = {}
    for s in StudySession.query.filter(StudySession.user_id == user_id, StudySession.start_time >= since).all():
        sid = str(s.subject_id)
        if sid not in agg: agg[sid] = {'minutes': 0, 'str': ''}
        agg[sid]['minutes'] += s.duration_minutes or 0
    for sid in agg: agg[sid]['str'] = fmt_duration(agg[sid]['minutes'])
    return jsonify({'labels': day_names, 'hours': [round(m/60,2) for m in mins],
                    'total_minutes': total, 'total_str': fmt_duration(total),
                    'today_index': today.weekday(), 'subject_stats': agg})

@app.route('/api/all-weekly-stats')
@login_required
def all_weekly_stats():
    today = (datetime.utcnow() - timedelta(hours=3)).date(); week_start = today - timedelta(days=today.weekday())
    day_names = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']; result = {'labels': day_names, 'datasets': []}
    for u in User.query.all():
        mins = [0.0] * 7
        for s in StudySession.query.filter(StudySession.user_id == u.id,
            StudySession.start_time >= datetime.combine(week_start, datetime.min.time())).all():
            idx = (s.start_time.date() - week_start).days
            if 0 <= idx < 7: mins[idx] += s.duration_minutes or 0
        result['datasets'].append({'label': u.display_name, 'color': u.avatar_color,
                                    'data': [round(m/60,2) for m in mins]})
    return jsonify(result)

@app.route('/api/my-stats')
@login_required
def my_stats():
    user_id = session['user_id']; today = (datetime.utcnow() - timedelta(hours=3)).date()
    week_start = today - timedelta(days=today.weekday())
    today_min = sum(s.duration_minutes or 0 for s in StudySession.query.filter(
        StudySession.user_id == user_id,
        StudySession.start_time >= datetime.combine(today, datetime.min.time())).all())
    week_min = sum(s.duration_minutes or 0 for s in StudySession.query.filter(
        StudySession.user_id == user_id,
        StudySession.start_time >= datetime.combine(week_start, datetime.min.time())).all())
    return jsonify({'today_str': fmt_duration(today_min), 'today_min': today_min,
                    'week_str': fmt_duration(week_min), 'week_min': week_min,
                    'streak': get_streak(user_id), 'goals': get_goals_progress(user_id)})

@app.route('/api/user-stats/<int:uid>')
@login_required
def user_stats(uid):
    today = (datetime.utcnow() - timedelta(hours=3)).date(); week_start = today - timedelta(days=today.weekday())
    day_names = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']
    days = {(week_start + timedelta(days=i)).isoformat(): 0.0 for i in range(7)}
    week_sessions = StudySession.query.filter(StudySession.user_id == uid,
        StudySession.start_time >= datetime.combine(week_start, datetime.min.time())).all()
    for s in week_sessions:
        key = s.start_time.date().isoformat()
        if key in days: days[key] += s.duration_minutes or 0
    mins = list(days.values()); total = sum(mins)
    day_subject = {i: {} for i in range(7)}
    for s in week_sessions:
        idx = (s.start_time.date() - week_start).days
        if 0 <= idx < 7:
            key = f"{s.subject.emoji} {s.subject.name}"
            if key not in day_subject[idx]: day_subject[idx][key] = {'minutes': 0, 'color': s.subject.color}
            day_subject[idx][key]['minutes'] += s.duration_minutes or 0
    since = datetime.utcnow() - timedelta(days=30); agg = {}
    for s in StudySession.query.filter(StudySession.user_id == uid, StudySession.start_time >= since).all():
        key = f"{s.subject.emoji} {s.subject.name}"
        if key not in agg: agg[key] = {'minutes': 0, 'color': s.subject.color}
        agg[key]['minutes'] += s.duration_minutes or 0
    subjects_sorted = sorted(agg.items(), key=lambda x: -x[1]['minutes'])
    recent = StudySession.query.filter_by(user_id=uid).order_by(StudySession.start_time.desc()).limit(10).all()
    user = User.query.get(uid)
    return jsonify({
        'user': {'id': user.id, 'display_name': user.display_name, 'avatar_color': user.avatar_color},
        'streak': get_streak(uid),
        'week': {'labels': day_names, 'hours': [round(m/60,2) for m in mins],
                 'minutes': [round(m,1) for m in mins], 'total_str': fmt_duration(total),
                 'today_index': today.weekday(),
                 'day_subjects': {str(i): {k: {'minutes': round(v['minutes'],1), 'str': fmt_duration(v['minutes']), 'color': v['color']} for k,v in day_subject[i].items()} for i in range(7)}},
        'subjects': [{'name': k, 'minutes': v['minutes'], 'str': fmt_duration(v['minutes']), 'color': v['color']} for k,v in subjects_sorted],
        'recent': [{'subject': s.subject.name, 'emoji': s.subject.emoji,
                    'date_str': s.start_time.strftime('%d/%m'), 'weekday': day_names[s.start_time.weekday()],
                    'time_str': s.start_time.strftime('%H:%M'),
                    'duration_str': fmt_duration(s.duration_minutes or 0),
                    'duration_min': round(s.duration_minutes or 0, 1)} for s in recent]
    })

@app.route('/api/goals', methods=['GET'])
@login_required
def get_goals():
    return jsonify({'goals': get_goals_progress(session['user_id'])})

@app.route('/api/goals/add', methods=['POST'])
@login_required
def add_goal():
    data = request.get_json()
    g = StudyGoal(user_id=session['user_id'], subject_id=data.get('subject_id') or None,
                  period=data.get('period', 'week'), minutes=float(data.get('minutes', 60)))
    db.session.add(g); db.session.commit()
    return jsonify({'success': True, 'id': g.id})

@app.route('/api/goals/delete/<int:gid>', methods=['DELETE'])
@login_required
def delete_goal(gid):
    g = StudyGoal.query.filter_by(id=gid, user_id=session['user_id']).first()
    if g: db.session.delete(g); db.session.commit()
    return jsonify({'success': True})

@app.route('/api/notes/add', methods=['POST'])
@login_required
def add_note():
    data = request.get_json()
    n = StudyNote(user_id=session['user_id'], session_id=data.get('session_id'),
                  content=data.get('content', '').strip())
    if not n.content: return jsonify({'success': False})
    db.session.add(n); db.session.commit()
    return jsonify({'success': True, 'id': n.id})

@app.route('/api/notes', methods=['GET'])
@login_required
def get_notes():
    notes = StudyNote.query.filter_by(user_id=session['user_id'])\
        .order_by(StudyNote.created_at.desc()).limit(20).all()
    return jsonify({'notes': [{'id': n.id, 'content': n.content,
        'date': n.created_at.strftime('%d/%m %H:%M')} for n in notes]})


@app.route('/api/recent-sessions-full')
@login_required
def recent_sessions_full():
    sessions = (StudySession.query.filter_by(user_id=session['user_id'])
                .order_by(StudySession.start_time.desc()).limit(15).all())
    # Convert UTC to BRT for display
    def to_brt(dt):
        return dt - timedelta(hours=3) if dt else dt
    return jsonify({'sessions': [{
        'id': s.id,
        'subject': s.subject.name, 'emoji': s.subject.emoji,
        'subject_id': s.subject_id,
        'date_str': to_brt(s.start_time).strftime('%d/%m %H:%M'),
        'start_raw': to_brt(s.start_time).strftime('%Y-%m-%dT%H:%M:%S'),
        'end_raw': to_brt(s.end_time).strftime('%Y-%m-%dT%H:%M:%S') if s.end_time else '',
        'duration_str': fmt_duration(s.duration_minutes or 0)
    } for s in sessions]})

@app.route('/api/session/<int:sid>', methods=['DELETE'])
@login_required
def delete_session(sid):
    s = StudySession.query.filter_by(id=sid, user_id=session['user_id']).first()
    if not s: return jsonify({'success': False, 'error': 'Não encontrado'})
    db.session.delete(s); db.session.commit()
    return jsonify({'success': True})

@app.route('/api/session/<int:sid>', methods=['PUT'])
@login_required
def edit_session(sid):
    s = StudySession.query.filter_by(id=sid, user_id=session['user_id']).first()
    if not s: return jsonify({'success': False, 'error': 'Não encontrado'})
    data = request.get_json()
    try:
        start_time = datetime.fromisoformat(data['start_time']) + timedelta(hours=3)
        end_time   = datetime.fromisoformat(data['end_time']) + timedelta(hours=3)
        duration   = (end_time - start_time).total_seconds() / 60
        if duration <= 0: return jsonify({'success': False, 'error': 'Duração inválida'})
        s.start_time = start_time; s.end_time = end_time
        s.duration_minutes = duration
        if data.get('subject_id'): s.subject_id = data['subject_id']
        db.session.commit()
        return jsonify({'success': True, 'duration_str': fmt_duration(duration)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/recent-sessions')
@login_required
def recent_sessions():
    sessions = (StudySession.query.filter_by(user_id=session['user_id'])
                .order_by(StudySession.start_time.desc()).limit(8).all())
    return jsonify({'sessions': [{'subject': s.subject.name, 'emoji': s.subject.emoji,
        'date_str': s.start_time.strftime('%d/%m %H:%M'),
        'duration_str': fmt_duration(s.duration_minutes or 0)} for s in sessions]})

@app.route('/api/add-subject', methods=['POST'])
@login_required
def add_subject():
    data = request.get_json()
    subj = Subject(name=data['name'], color=data.get('color','#3b82f6'),
                   emoji=data.get('emoji','📚'), user_id=session['user_id'])
    db.session.add(subj); db.session.commit()
    return jsonify({'success': True, 'id': subj.id, 'name': subj.name, 'color': subj.color, 'emoji': subj.emoji})

@app.route('/api/update-profile', methods=['POST'])
@login_required
def update_profile():
    data = request.get_json(); user = User.query.get(session['user_id'])
    if 'display_name' in data and data['display_name'].strip(): user.display_name = data['display_name'].strip()
    if 'avatar_color' in data: user.avatar_color = data['avatar_color']
    if 'password' in data and data['password'].strip(): user.password_hash = generate_password_hash(data['password'].strip())
    db.session.commit()
    return jsonify({'success': True, 'display_name': user.display_name})

@app.route('/api/set-api-key', methods=['POST'])
@login_required
def set_api_key():
    data = request.get_json(); key = data.get('api_key', '').strip()
    cfg = load_config(); cfg['api_key'] = key; save_config(cfg)
    return jsonify({'success': True})

@app.route('/api/push/vapid-public-key')
@login_required
def vapid_public_key():
    cfg = load_config()
    key = os.environ.get('VAPID_PUBLIC_KEY', cfg.get('vapid_public', ''))
    return jsonify({'key': key})

@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    user_id = session['user_id']; data = request.get_json(); sub_json = jsonlib.dumps(data)
    existing = PushSubscription.query.filter_by(user_id=user_id).first()
    if existing: existing.subscription = sub_json
    else: db.session.add(PushSubscription(user_id=user_id, subscription=sub_json))
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    PushSubscription.query.filter_by(user_id=session['user_id']).delete()
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/push/set-vapid', methods=['POST'])
@login_required
def set_vapid():
    data = request.get_json(); cfg = load_config()
    cfg['vapid_public'] = data.get('public_key', '')
    cfg['vapid_private'] = data.get('private_key', '')
    cfg['vapid_email']  = data.get('email', 'mailto:admin@studytogether.app')
    save_config(cfg); return jsonify({'success': True})

@app.route('/api/study-guide', methods=['POST'])
@login_required
def study_guide():
    import anthropic as ant
    data = request.get_json()
    mode = data.get('mode', 'enem'); discipline = data.get('discipline', '').strip(); topic = data.get('topic', '').strip()
    api_key = get_api_key()
    if not api_key: return jsonify({'error': 'api_key_missing'}), 400
    if not discipline: return jsonify({'error': 'Informe a disciplina'}), 400
    if mode == 'enem':
        context = "ENEM (Exame Nacional do Ensino Médio)"; extra = "Baseie-se na Matriz de Referência do ENEM, nas provas de 2010-2024."
    else:
        context = "UFCG (Universidade Federal de Campina Grande) — Bacharelado em Ciência da Computação"; extra = "Considere o estilo das provas de CC da UFCG."
    topic_part = f"Tópico específico: {topic}" if topic else "Tópico: Conteúdo geral completo da disciplina"
    prompt = f"""Você é um especialista em educação brasileira e orientador de estudos de alta performance.\n\nContexto: {context}\nDisciplina: {discipline}\n{topic_part}\n{extra}\n\nRetorne SOMENTE um JSON válido, sem nenhum texto fora do JSON:\n\n{{\n  "relevancia": "alta|média|baixa",\n  "descricao_relevancia": "2-3 frases objetivas",\n  "topicos_mais_cobrados": [\n    {{"topico": "Nome", "peso": "alto|médio|baixo", "frequencia": "Ex: cai em ~70% das provas", "descricao": "O que é testado"}}\n  ],\n  "padrao_questoes": {{\n    "tipo": "múltipla escolha|dissertativa|misto|implementação",\n    "abordagem": "Como as questões são formuladas",\n    "armadilhas": ["Armadilha 1", "Armadilha 2", "Armadilha 3"]\n  }},\n  "roteiro_estudo": [\n    {{"etapa": 1, "titulo": "Título", "acao": "O que fazer", "tempo": "X horas"}}\n  ],\n  "conceitos_chave": [\n    {{"conceito": "Nome", "importancia": "alta|média", "memorizar": "Frase-chave"}}\n  ],\n  "exemplo_questao": {{\n    "enunciado": "Enunciado completo",\n    "alternativas": ["(A) texto", "(B) texto", "(C) texto", "(D) texto", "(E) texto"],\n    "gabarito": "letra correta",\n    "resolucao": "Resolução passo a passo"\n  }},\n  "recursos": [\n    {{"tipo": "videoaula|livro|site|lista", "nome": "Nome", "detalhe": "Por que é o melhor"}}\n  ]\n}}\n\nMáximo: 5 tópicos, 5 conceitos, 4 etapas, 3 recursos."""
    try:
        client = ant.Anthropic(api_key=api_key)
        message = client.messages.create(model="claude-opus-4-5", max_tokens=2000, messages=[{"role": "user", "content": prompt}])
        text = message.content[0].text.strip()
        if '```' in text:
            for part in text.split('```'):
                part = part.strip().lstrip('json').strip()
                if part.startswith('{'): text = part; break
        result = jsonlib.loads(text)
        result.update({'discipline': discipline, 'topic': topic, 'mode': mode})
        return jsonify(result)
    except jsonlib.JSONDecodeError as e:
        return jsonify({'error': f'Erro ao processar resposta: {str(e)}'}), 500
    except Exception as e:
        err = str(e)
        if any(x in err.lower() for x in ['api_key','authentication','unauthorized','invalid_api']):
            return jsonify({'error': 'api_key_invalid'}), 401
        return jsonify({'error': f'Erro: {err}'}), 500

@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

def create_pwa_icons():
    icons_dir = os.path.join(os.path.dirname(__file__), 'static', 'icons')
    os.makedirs(icons_dir, exist_ok=True)
    try:
        from PIL import Image, ImageDraw
        import math
        for size in [192, 512]:
            path = os.path.join(icons_dir, f'icon-{size}.png')
            if os.path.exists(path): continue
            img = Image.new('RGBA', (size, size), '#080c14')
            draw = ImageDraw.Draw(img)
            pad = size // 10
            draw.rounded_rectangle([pad, pad, size-pad, size-pad], radius=size//7, fill='#141d30')
            cx, cy = size//2, size//2
            r_outer = int(size * 0.30); r_inner = int(size * 0.13)
            pts = []
            for i in range(8):
                a = math.pi * i / 4 - math.pi/4
                rad = r_outer if i % 2 == 0 else r_inner
                pts.append((cx + rad*math.cos(a), cy + rad*math.sin(a)))
            draw.polygon(pts, fill='#f0a500')
            img.save(path, 'PNG')
        print("✅  Ícones PWA criados.")
    except ImportError:
        print("⚠️   Pillow não disponível — ícones PWA ignorados.")
    except Exception as e:
        print(f"⚠️   {e}")

def init_db():
    db.create_all()
    create_pwa_icons()
    if User.query.count() > 0: return
    guilherme = User(username='guilherme', display_name='Guilherme',
                     password_hash=generate_password_hash('guilherme123'), avatar_color='#3b82f6')
    parceira  = User(username='parceira',  display_name='Valessa',
                     password_hash=generate_password_hash('parceira123'),  avatar_color='#f43f5e')
    db.session.add_all([guilherme, parceira]); db.session.commit()
    g_subjects = [
        Subject(name='LEDA',    color='#8b5cf6', emoji='🌳', user_id=guilherme.id),
        Subject(name='Cálculo', color='#3b82f6', emoji='∫',  user_id=guilherme.id),
        Subject(name='Python',  color='#10b981', emoji='🐍', user_id=guilherme.id),
        Subject(name='LP1',     color='#f59e0b', emoji='☕', user_id=guilherme.id),
        Subject(name='Inglês',  color='#ef4444', emoji='🗣️', user_id=guilherme.id),
    ]
    p_subjects = [
        Subject(name='Matemática', color='#3b82f6', emoji='📐', user_id=parceira.id),
        Subject(name='Português',  color='#f43f5e', emoji='📝', user_id=parceira.id),
        Subject(name='Biologia',   color='#10b981', emoji='🧬', user_id=parceira.id),
        Subject(name='História',   color='#f59e0b', emoji='📜', user_id=parceira.id),
        Subject(name='Química',    color='#a855f7', emoji='⚗️', user_id=parceira.id),
        Subject(name='Física',     color='#06b6d4', emoji='⚡', user_id=parceira.id),
        Subject(name='Inglês',     color='#ef4444', emoji='🗣️', user_id=parceira.id),
    ]
    db.session.add_all(g_subjects + p_subjects); db.session.commit()
    print("✅  Banco criado com usuários padrão.")

if __name__ == '__main__':
    with app.app_context(): init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
