from flask import Flask, render_template, request, session, redirect, url_for, flash
import os
import hashlib
import time
from db import get_db
import bcrypt
from werkzeug.utils import secure_filename
from datetime import datetime
import base64


app = Flask(__name__)
app.secret_key = "secret123"
app.config['UPLOAD_FOLDER'] = 'uploads'

# Create uploads folder if not exists
if not os.path.exists('uploads'):
    os.makedirs('uploads')


# ---------------- HOME ----------------
@app.route('/')
def home():
    return render_template("home.html")


# ---------------- REGISTER ----------------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']

        if role not in ['user', 'admin']:
            return "Invalid Role Selected"

        db = get_db()
        cursor = db.cursor()

        # HASH PASSWORD
        hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        existing_user = cursor.fetchone()

        if existing_user:
            return "Username already exists ❌"
        
        cursor.execute(
            "INSERT INTO users (username, password, role) VALUES (%s, %s, %s)",
            (username, hashed_pw.decode(), role)
        )
        db.commit()

        return redirect(url_for('login'))

    return render_template('register.html')


# ---------------- LOGIN ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        cursor = db.cursor(dictionary=True)

        cursor.execute(
            "SELECT * FROM users WHERE username=%s",
            (username,)
        )
        user = cursor.fetchone()

        if user and bcrypt.checkpw(password.encode(), user['password'].encode()):
            session['user_id'] = user['id'] 
            session['username'] = user['username']
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        else:
            return "Invalid Credentials ❌"

    return render_template('login.html')


# ---------------- DASHBOARD ----------------
@app.route('/dashboard')
def dashboard():

    if "username" not in session:
        return redirect(url_for("login"))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    if session.get("role") == "admin":
        cursor.execute("""
            SELECT id, username, filename, status, uploaded_at
            FROM documents
            ORDER BY uploaded_at DESC
        """)
    else:
        cursor.execute("""
            SELECT id, username, filename, status, uploaded_at
            FROM documents
            WHERE username=%s
            ORDER BY uploaded_at DESC
        """, (session["username"],))

    docs = cursor.fetchall()

    cursor.close()
    db.close()

    return render_template("dashboard.html", docs=docs)



# ---------------- LOGOUT ----------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------- UPLOAD ----------------
@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if "username" not in session:
        return redirect(url_for("login"))

    if request.method == 'POST':

        import base64

        file = request.files.get('file')
        captured_image = request.form.get('captured_image')

        # 🔴 If image captured from camera
        if captured_image:

            image_data = captured_image.split(",")[1]
            image_bytes = base64.b64decode(image_data)

            filename = f"camera_{int(time.time())}.png"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

            with open(file_path, "wb") as f:
                f.write(image_bytes)

        # 🔵 Normal file upload
        elif file and file.filename != "":

            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

        else:
            flash("No file selected!", "danger")
            return redirect(request.url)

        # 🔹 Generate SHA256 hash
        hash_obj = hashlib.sha256()
        with open(file_path, "rb") as f:
            hash_obj.update(f.read())
        doc_hash = hash_obj.hexdigest()

        # 🔹 Connect DB
        db = get_db()
        cursor = db.cursor()

        # 🔹 Insert into documents table
        cursor.execute("""
            INSERT INTO documents (username, filename, status, uploaded_at)
            VALUES (%s, %s, %s, NOW())
        """, (session["username"], filename, "Pending"))
        db.commit()

        doc_id = cursor.lastrowid

        # 🔹 Get previous hash
        cursor.execute("SELECT doc_hash FROM blockchain ORDER BY id DESC LIMIT 1")
        prev = cursor.fetchone()
        prev_hash = prev[0] if prev else "0"

        # 🔹 Insert into blockchain table
        cursor.execute("""
            INSERT INTO blockchain (document_id, doc_hash, prev_hash, timestamp)
            VALUES (%s, %s, %s, %s)
        """, (doc_id, doc_hash, prev_hash, datetime.now()))
        db.commit()

        flash("✅ Document uploaded and added to blockchain successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("upload.html")
#--------------testcamera------------
@app.route('/testcamera')
def testcamera():
    return render_template("testcamera.html")

# ---------------- ADMIN PANEL ----------------
@app.route('/admin')
def admin():
    if 'role' not in session or session['role'] != 'admin':
        return "Access Denied ❌"

    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()

    cursor.execute("SELECT * FROM documents")
    docs = cursor.fetchall()

    return render_template('admin.html', docs=docs)


# ---------------- APPROVE ----------------
@app.route('/approve/<int:id>', methods=['POST'])
def approve(id):

    # 🔐 Check login
    if "username" not in session:
        return redirect(url_for("login"))

    # 🔐 Check admin role
    if session.get("role") != "admin":
        flash("Unauthorized Access!", "danger")
        return redirect(url_for("dashboard"))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    # 🔹 Get document
    cursor.execute("SELECT * FROM documents WHERE id=%s", (id,))
    doc = cursor.fetchone()

    if not doc:
        flash("Document not found!", "danger")
        return redirect(url_for("dashboard"))

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], doc['filename'])

    # 🔹 Recalculate hash
    hash_obj = hashlib.sha256()
    with open(file_path, "rb") as f:
        hash_obj.update(f.read())
    current_hash = hash_obj.hexdigest()

    # 🔹 Get blockchain hash
    cursor.execute("SELECT doc_hash FROM blockchain WHERE document_id=%s", (id,))
    block = cursor.fetchone()

    if block and block['doc_hash'] == current_hash:
        status = "Approved"
        audit_result = "APPROVED"
        flash("✅ Document Approved Successfully!", "success")
    else:
        status = "Tampered"
        audit_result = "TAMPER DETECTED"
        flash("❌ Document Tampered! Cannot Approve.", "danger")

    # 🔹 Update status
    cursor.execute(
        "UPDATE documents SET status=%s WHERE id=%s",
        (status, id)
    )

    # 🔹 Insert audit log
    cursor.execute("""
        INSERT INTO audit_logs (user_id, document_id, action, result)
        VALUES (%s, %s, %s, %s)
    """, (session['user_id'], id, "APPROVE", audit_result))

    db.commit()

    return redirect(url_for('dashboard'))



# ---------------- REJECT ----------------
@app.route('/reject/<int:id>')
def reject(id):
    if 'role' not in session or session['role'] != 'admin':
        return "Access Denied ❌"

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        "UPDATE documents SET status='Rejected' WHERE id=%s",
        (id,)
    )
    db.commit()

    return redirect(url_for('admin'))
#-------verify----------
@app.route('/verify/<int:doc_id>', methods=['POST'])
def verify(doc_id):

    if "username" not in session:
        return redirect(url_for("login"))
    
    db = get_db()
    cursor = db.cursor()

    # Get document info
    cursor.execute("SELECT filename FROM documents WHERE id=%s", (doc_id,))
    doc = cursor.fetchone()

    if not doc:
        return "Document not found"

    filename = doc[0]
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    # Generate new hash
    hash_obj = hashlib.sha256()
    with open(file_path, "rb") as f:
        hash_obj.update(f.read())

    new_hash = hash_obj.hexdigest()

    # Get stored blockchain hash
    cursor.execute("SELECT doc_hash FROM blockchain WHERE document_id=%s", (doc_id,))
    block = cursor.fetchone()

    if not block:
        return "Blockchain record not found"

    stored_hash = block[0]

    if new_hash == stored_hash:
        status = "Verified"
        audit_result = "AUTHENTIC"
        result = "✅ Document Verified"
    else:
        status = "Tampered"
        audit_result = "TAMPERED"
        result = "❌ Document has been Tampered"

    # 🔥 UPDATE STATUS HERE
    cursor.execute(
        "UPDATE documents SET status=%s WHERE id=%s",
        (status, doc_id)
    )

    # 🔹 Insert into audit_logs
    cursor.execute("""
        INSERT INTO audit_logs (user_id, document_id, action, result)
        VALUES (%s, %s, %s, %s)
    """, (session['user_id'], doc_id, "VERIFY", audit_result))

    db.commit()
    return render_template("verify.html", result=result)

#--------delete--------
@app.route('/delete/<int:doc_id>', methods=['POST'])
def delete(doc_id):

    if "username" not in session:
        return redirect(url_for("login"))

    # 🔐 Admin only
    if session.get("role") != "admin":
        return "Access Denied ❌"

    db = get_db()
    cursor = db.cursor()

    try:
        # 1️⃣ Get filename
        cursor.execute("SELECT filename FROM documents WHERE id=%s", (doc_id,))
        doc = cursor.fetchone()

        if not doc:
            return "Document not found"

        filename = doc[0]
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        # 2️⃣ Delete physical file
        if os.path.exists(file_path):
            os.remove(file_path)

        # 3️⃣ Delete from blockchain
        cursor.execute("DELETE FROM blockchain WHERE document_id=%s", (doc_id,))

        # 4️⃣ Delete from audit_logs
        cursor.execute("DELETE FROM audit_logs WHERE document_id=%s", (doc_id,))

        # 5️⃣ Delete from documents
        cursor.execute("DELETE FROM documents WHERE id=%s", (doc_id,))

        db.commit()

    except Exception as e:
        db.rollback()
        return f"Error occurred: {str(e)}"

    finally:
        cursor.close()
        db.close()

    return redirect(url_for("dashboard"))

 
#----audit---
@app.route("/audit")
def audit():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT a.id, u.username, a.document_id, a.action, a.result, a.timestamp
        FROM audit_logs a
        JOIN users u ON a.user_id = u.id
        ORDER BY a.timestamp DESC
    """)

    logs = cursor.fetchall()
    return render_template("audit.html", logs=logs)

# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(debug=True)
