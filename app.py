from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import os, json as jsonlib

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'studytogether-secret-2024-xk9')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///study.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.json')

# ─── MODELS ─────────────────────────────────────────────────────────────────
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

# ─── HELPERS ────────────────────────────────────────────────────────────────
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
    """Envia push notification para todas as subscriptions de um usuário."""
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
                webpush(
                    subscription_info=sub_info,
                    data=data,
                    vapid_private_key=vapid_private,
                    vapid_claims={'sub': vapid_email}
                )
            except WebPushException as e:
                # Subscription expirada — remove
                if e.response and e.response.status_code in [404, 410]:
                    db.session.delete(sub)
                    db.session.commit()
    except Exception:
        pass  # Push é opcional — não quebra o app se falhar

# ─── PAGE ROUTES ─────────────────────────────────────────────────────────────
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

# ─── API ─────────────────────────────────────────────────────────────────────
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

    # Notifica o parceiro
    user    = User.query.get(user_id)
    subject = Subject.query.get(data['subject_id'])
    partner = User.query.filter(User.id != user_id).first()
    if partner:
        send_push_to_user(
            partner.id,
            f"{subject.emoji} {user.display_name} começou a estudar!",
            f"Estudando {subject.name} agora. Bora junto? 📚"
        )

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

    # Notifica o parceiro
    user    = User.query.get(user_id)
    partner = User.query.filter(User.id != user_id).first()
    if partner:
        send_push_to_user(
            partner.id,
            f"⏹ {user.display_name} encerrou a sessão",
            f"Estudou por {fmt_duration(duration)}. Ótimo trabalho! ✨"
        )

    return jsonify({'success': True, 'duration_minutes': duration, 'duration_str': fmt_duration(duration)})

@app.route('/api/weekly-stats')
@login_required
def weekly_stats():
    user_id = session['user_id']; today = datetime.utcnow().date()
    week_start = today - timedelta(days=today.weekday()); day_names = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']
    days = {(week_start + timedelta(days=i)).isoformat(): 0.0 for i in range(7)}
    for s in StudySession.query.filter(StudySession.user_id == user_id,
        StudySession.start_time >= datetime.combine(week_start, datetime.min.time())).all():
        key = s.start_time.date().isoformat()
        if key in days: days[key] += s.duration_minutes or 0
    mins = list(days.values()); total = sum(mins)
    today_idx = today.weekday()
    # subject stats
    since = datetime.utcnow() - timedelta(days=30); agg = {}
    for s in StudySession.query.filter(StudySession.user_id == user_id,
        StudySession.start_time >= since).all():
        sid = str(s.subject_id)
        if sid not in agg: agg[sid] = {'minutes': 0, 'str': ''}
        agg[sid]['minutes'] += s.duration_minutes or 0
    for sid in agg: agg[sid]['str'] = fmt_duration(agg[sid]['minutes'])
    return jsonify({'labels': day_names, 'hours': [round(m/60,2) for m in mins],
                    'total_minutes': total, 'total_str': fmt_duration(total),
                    'today_index': today_idx, 'subject_stats': agg})

@app.route('/api/subject-stats')
@login_required
def subject_stats():
    user_id = session['user_id']; since = datetime.utcnow() - timedelta(days=30); agg = {}
    for s in StudySession.query.filter(StudySession.user_id == user_id,
        StudySession.start_time >= since).all():
        name = s.subject.name
        if name not in agg: agg[name] = {'minutes': 0, 'color': s.subject.color}
        agg[name]['minutes'] += s.duration_minutes or 0
    items = sorted(agg.items(), key=lambda x: -x[1]['minutes'])
    return jsonify({'labels': [k for k,v in items], 'hours': [round(v['minutes']/60,2) for k,v in items],
                    'colors': [v['color'] for k,v in items]})

@app.route('/api/all-weekly-stats')
@login_required
def all_weekly_stats():
    today = datetime.utcnow().date(); week_start = today - timedelta(days=today.weekday())
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


@app.route('/api/manual-session', methods=['POST'])
@login_required
def manual_session():
    data = request.get_json()
    user_id = session['user_id']
    try:
        start_time = datetime.fromisoformat(data['start_time'])
        end_time   = datetime.fromisoformat(data['end_time'])
        duration   = (end_time - start_time).total_seconds() / 60
        if duration <= 0: return jsonify({'success': False, 'error': 'Duração inválida'})
        s = StudySession(user_id=user_id, subject_id=data['subject_id'],
                         start_time=start_time, end_time=end_time, duration_minutes=duration)
        db.session.add(s); db.session.commit()
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

# ─── PUSH NOTIFICATION API ────────────────────────────────────────────────────
@app.route('/api/push/vapid-public-key')
@login_required
def vapid_public_key():
    cfg = load_config()
    key = os.environ.get('VAPID_PUBLIC_KEY', cfg.get('vapid_public', ''))
    return jsonify({'key': key})

@app.route('/api/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    user_id = session['user_id']
    data = request.get_json()
    sub_json = jsonlib.dumps(data)
    # Atualiza se já existe, cria se não
    existing = PushSubscription.query.filter_by(user_id=user_id).first()
    if existing:
        existing.subscription = sub_json
    else:
        db.session.add(PushSubscription(user_id=user_id, subscription=sub_json))
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    user_id = session['user_id']
    PushSubscription.query.filter_by(user_id=user_id).delete()
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/push/set-vapid', methods=['POST'])
@login_required
def set_vapid():
    data = request.get_json()
    cfg = load_config()
    cfg['vapid_public']  = data.get('public_key', '')
    cfg['vapid_private'] = data.get('private_key', '')
    cfg['vapid_email']   = data.get('email', 'mailto:admin@studytogether.app')
    save_config(cfg)
    return jsonify({'success': True})

# ─── STUDY GUIDE API ──────────────────────────────────────────────────────────
@app.route('/api/study-guide', methods=['POST'])
@login_required
def study_guide():
    import anthropic as ant
    data = request.get_json()
    mode = data.get('mode', 'enem')
    discipline = data.get('discipline', '').strip()
    topic = data.get('topic', '').strip()

    api_key = get_api_key()
    if not api_key: return jsonify({'error': 'api_key_missing'}), 400
    if not discipline: return jsonify({'error': 'Informe a disciplina'}), 400

    if mode == 'enem':
        context = "ENEM (Exame Nacional do Ensino Médio)"
        extra = "Baseie-se na Matriz de Referência do ENEM, nas provas de 2010-2024."
    else:
        context = "UFCG (Universidade Federal de Campina Grande) — Bacharelado em Ciência da Computação"
        extra = "Considere o estilo das provas de CC da UFCG."

    topic_part = f"Tópico específico: {topic}" if topic else "Tópico: Conteúdo geral completo da disciplina"

    prompt = f"""Você é um especialista em educação brasileira e orientador de estudos de alta performance.

Contexto: {context}
Disciplina: {discipline}
{topic_part}
{extra}

Retorne SOMENTE um JSON válido, sem nenhum texto fora do JSON:

{{
  "relevancia": "alta|média|baixa",
  "descricao_relevancia": "2-3 frases objetivas",
  "topicos_mais_cobrados": [
    {{"topico": "Nome", "peso": "alto|médio|baixo", "frequencia": "Ex: cai em ~70% das provas", "descricao": "O que é testado"}}
  ],
  "padrao_questoes": {{
    "tipo": "múltipla escolha|dissertativa|misto|implementação",
    "abordagem": "Como as questões são formuladas",
    "armadilhas": ["Armadilha 1", "Armadilha 2", "Armadilha 3"]
  }},
  "roteiro_estudo": [
    {{"etapa": 1, "titulo": "Título", "acao": "O que fazer", "tempo": "X horas"}}
  ],
  "conceitos_chave": [
    {{"conceito": "Nome", "importancia": "alta|média", "memorizar": "Frase-chave"}}
  ],
  "exemplo_questao": {{
    "enunciado": "Enunciado completo",
    "alternativas": ["(A) texto", "(B) texto", "(C) texto", "(D) texto", "(E) texto"],
    "gabarito": "letra correta",
    "resolucao": "Resolução passo a passo"
  }},
  "recursos": [
    {{"tipo": "videoaula|livro|site|lista", "nome": "Nome", "detalhe": "Por que é o melhor"}}
  ]
}}

Máximo: 5 tópicos, 5 conceitos, 4 etapas, 3 recursos."""

    try:
        client = ant.Anthropic(api_key=api_key)
        message = client.messages.create(model="claude-opus-4-5", max_tokens=2000,
                                          messages=[{"role": "user", "content": prompt}])
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

# ─── PWA ─────────────────────────────────────────────────────────────────────
@app.route('/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js')
    response.headers['Service-Worker-Allowed'] = '/'
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

# ─── INIT ─────────────────────────────────────────────────────────────────────
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
    parceira  = User(username='parceira',  display_name='Parceira',
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
