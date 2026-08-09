from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)

# CORS — gt.tc ve diğer originlerden gelen isteklere izin ver
CORS(app)

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'appointments.db')


def get_db():
    """Veritabanı bağlantısı oluştur."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Veritabanı tablolarını oluştur (yoksa)."""
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            name TEXT,
            phone TEXT,
            status TEXT NOT NULL DEFAULT 'booked',
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, time)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS day_closures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()


@app.route('/')
def index():
    """Sunucu sağlık kontrolü."""
    return jsonify({
        "status": "ok",
        "message": "Beatrix Berber Randevu API çalışıyor.",
        "time": datetime.now().isoformat()
    })


@app.route('/api/appointments', methods=['GET'])
def get_appointments():
    """Belirli bir tarihin randevularını getir."""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({"error": "Tarih parametresi gerekli. Örnek: ?date=2026-08-10"}), 400

    conn = get_db()
    rows = conn.execute(
        'SELECT time, name, phone, status, note FROM appointments WHERE date = ?',
        (date_str,)
    ).fetchall()
    conn.close()

    # Frontend'in beklediği format: { "10:00": { "name": "...", "phone": "...", "status": "booked" }, ... }
    result = {}
    for row in rows:
        result[row['time']] = {
            "name": row['name'],
            "phone": row['phone'],
            "status": row['status'],
            "note": row['note']
        }

    return jsonify(result)


@app.route('/api/book', methods=['POST'])
def book_appointment():
    """Yeni randevu oluştur."""
    data = request.get_json()

    if not data:
        return jsonify({"message": "Geçersiz istek verisi."}), 400

    date_str = data.get('date')
    time_str = data.get('time')
    name = data.get('name')
    phone = data.get('phone')

    if not all([date_str, time_str, name, phone]):
        return jsonify({"message": "Tarih, saat, isim ve telefon bilgileri gereklidir."}), 400

    conn = get_db()
    try:
        # Mevcut randevu kontrolü
        existing = conn.execute(
            'SELECT * FROM appointments WHERE date = ? AND time = ? AND status = ?',
            (date_str, time_str, 'booked')
        ).fetchone()

        if existing:
            conn.close()
            return jsonify({"message": "Bu saat zaten dolu. Lütfen başka bir saat seçiniz."}), 409

        # Eğer iptal edilmiş bir randevu varsa, güncelle; yoksa yeni ekle
        cancelled = conn.execute(
            'SELECT * FROM appointments WHERE date = ? AND time = ?',
            (date_str, time_str)
        ).fetchone()

        if cancelled:
            conn.execute(
                'UPDATE appointments SET name = ?, phone = ?, status = ?, note = NULL WHERE date = ? AND time = ?',
                (name, phone, 'booked', date_str, time_str)
            )
        else:
            conn.execute(
                'INSERT INTO appointments (date, time, name, phone, status) VALUES (?, ?, ?, ?, ?)',
                (date_str, time_str, name, phone, 'booked')
            )

        conn.commit()
        conn.close()
        return jsonify({"message": f"Randevunuz başarıyla oluşturuldu. {date_str} tarihinde saat {time_str}'de bekliyoruz."}), 201

    except Exception as e:
        conn.close()
        return jsonify({"message": f"Bir hata oluştu: {str(e)}"}), 500


@app.route('/api/cancel', methods=['POST'])
def cancel_appointment():
    """Randevu iptal et."""
    data = request.get_json()

    if not data:
        return jsonify({"message": "Geçersiz istek verisi."}), 400

    date_str = data.get('date')
    time_str = data.get('time')
    note = data.get('note', 'Yönetim tarafından iptal edilmiştir.')

    if not all([date_str, time_str]):
        return jsonify({"message": "Tarih ve saat bilgileri gereklidir."}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            'SELECT * FROM appointments WHERE date = ? AND time = ?',
            (date_str, time_str)
        ).fetchone()

        if not existing:
            conn.close()
            return jsonify({"message": "Bu saatte randevu bulunamadı."}), 404

        conn.execute(
            'UPDATE appointments SET status = ?, note = ? WHERE date = ? AND time = ?',
            ('cancelled', note, date_str, time_str)
        )
        conn.commit()
        conn.close()
        return jsonify({"message": "Randevu başarıyla iptal edildi."}), 200

    except Exception as e:
        conn.close()
        return jsonify({"message": f"Bir hata oluştu: {str(e)}"}), 500


@app.route('/api/day-status', methods=['GET'])
def get_day_status():
    """Bir günün tatil olup olmadığını kontrol et."""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({"closed": False})

    conn = get_db()
    row = conn.execute('SELECT note FROM day_closures WHERE date = ?', (date_str,)).fetchone()
    conn.close()

    if row:
        return jsonify({"closed": True, "note": row['note']})
    return jsonify({"closed": False})


@app.route('/api/close-day', methods=['POST'])
def close_day():
    """Bir günü tatil ilan et."""
    data = request.get_json()
    if not data:
        return jsonify({"message": "Geçersiz istek verisi."}), 400

    date_str = data.get('date')
    note = data.get('note', 'Bugün hizmet verilmemektedir.')

    if not date_str:
        return jsonify({"message": "Tarih gereklidir."}), 400

    conn = get_db()
    try:
        # Günü kapat
        conn.execute(
            'INSERT OR REPLACE INTO day_closures (date, note) VALUES (?, ?)',
            (date_str, note)
        )
        # Mevcut randevuları iptal et
        conn.execute(
            'UPDATE appointments SET status = ?, note = ? WHERE date = ? AND status = ?',
            ('cancelled', note, date_str, 'booked')
        )
        conn.commit()
        conn.close()
        return jsonify({"message": f"{date_str} tatil ilan edildi."}), 200
    except Exception as e:
        conn.close()
        return jsonify({"message": f"Hata: {str(e)}"}), 500


@app.route('/api/reopen-day', methods=['POST'])
def reopen_day():
    """Tatil ilan edilen günü geri aç."""
    data = request.get_json()
    if not data:
        return jsonify({"message": "Geçersiz istek verisi."}), 400

    date_str = data.get('date')
    if not date_str:
        return jsonify({"message": "Tarih gereklidir."}), 400

    conn = get_db()
    try:
        # Gün kapatma kaydını sil
        conn.execute('DELETE FROM day_closures WHERE date = ?', (date_str,))
        # Tatil sebebiyle iptal edilmiş randevuları da sil (tekrar müsait olsun)
        conn.execute('DELETE FROM appointments WHERE date = ? AND status = ?', (date_str, 'cancelled'))
        conn.commit()
        conn.close()
        return jsonify({"message": f"{date_str} tekrar açıldı."}), 200
    except Exception as e:
        conn.close()
        return jsonify({"message": f"Hata: {str(e)}"}), 500


# Uygulama başlatılırken veritabanını oluştur
with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
