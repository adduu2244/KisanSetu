import random
import io
import csv
import calendar
from datetime import datetime, timedelta
from bson.objectid import ObjectId
from flask import send_file
from flask import Flask, render_template, redirect, url_for, request, jsonify
from flask_pymongo import PyMongo
from flask_bcrypt import Bcrypt
from flask_jwt_extended import (
    JWTManager, create_access_token,
    jwt_required, get_jwt_identity
)

app = Flask(__name__)

# =====================
# CONFIG
# =====================
app.config["MONGO_URI"] = "mongodb://localhost:27017/kisansetu"
app.config["JWT_SECRET_KEY"] = "kisansetu-secret-key"

mongo = PyMongo(app)
bcrypt = Bcrypt(app)
jwt = JWTManager(app)

@jwt.unauthorized_loader
def unauthorized_response(callback):
    return jsonify({"message": "Authorization header missing or invalid"}), 401

@jwt.invalid_token_loader
def invalid_token_response(reason):
    return jsonify({"message": f"Invalid token: {reason}"}), 401

@jwt.expired_token_loader
def expired_token_response(jwt_header, jwt_payload):
    return jsonify({"message": "Token has expired"}), 401

# =====================
# AUTH APIs
# =====================

# REGISTER
@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json

    hashed_pw = bcrypt.generate_password_hash(data['password']).decode('utf-8')

    user = {
        "name": data["name"],
        "mobile": data["mobile"],
        "password": hashed_pw
    }

    mongo.db.users.insert_one(user)

    return jsonify({"message": "User registered successfully"})


#LOGIN
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json

    user = mongo.db.users.find_one({
        "mobile": data["mobile"]
    })

    if user and bcrypt.check_password_hash(user["password"], data["password"]):
        token = create_access_token(identity=str(user["_id"]))
        return jsonify({"token": token, "name": user["name"]})

    return jsonify({"message": "Invalid credentials"}), 401

# =====================
# PRODUCT APIs
# =====================

# ADD PRODUCT
@app.route('/api/products', methods=['POST'])
@jwt_required()
def add_product():
    user_id = get_jwt_identity()
    data = request.json

    product = {
        "farmer_id": user_id,
        "name": data["name"],
        "price": data["price"],
        "quantity": data["quantity"],
        "category": data.get("category", "general")
    }

    mongo.db.products.insert_one(product)

    return jsonify({"message": "Product added successfully"})


# GET PRODUCTS
@app.route('/api/products', methods=['GET'])
@jwt_required()
def get_products():
    user_id = get_jwt_identity()

    products = list(mongo.db.products.find(
        {"farmer_id": user_id},
        {"_id": 0}
    ))

    return jsonify(products)


# DELETE PRODUCT
@app.route('/api/products/<name>', methods=['DELETE'])
@jwt_required()
def delete_product(name):
    user_id = get_jwt_identity()

    mongo.db.products.delete_one({
        "farmer_id": user_id,
        "name": name
    })

    return jsonify({"message": "Product deleted"})


# =====================
# ORDER APIs
# =====================

@app.route('/api/orders', methods=['GET'])
@jwt_required()
def get_orders():
    user_id = get_jwt_identity()
    orders = list(mongo.db.orders.find(
        {"farmer_id": user_id},
        {"_id": 0}
    ))
    return jsonify(orders)


@app.route('/api/orders', methods=['POST'])
@jwt_required()
def add_order():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"message": "Invalid JSON payload"}), 400

    try:
        quantity = float(data.get('quantity', 0))
        price_per_kg = float(data.get('price_per_kg', data.get('price', 0)))
    except (TypeError, ValueError):
        return jsonify({"message": "Quantity and price must be numeric"}), 400

    gross_value = quantity * price_per_kg

    order = {
        "farmer_id": user_id,
        "order_id": data.get('order_id', f"#KS-{random.randint(1000, 9999)}"),
        "customer_name": data.get('customer_name', ''),
        "location": data.get('location', ''),
        "crop_name": data.get('crop_name', ''),
        "quantity": quantity,
        "price_per_kg": price_per_kg,
        "status": data.get('status', 'Pending'),
        "gross_value": gross_value,
        "created_at": datetime.utcnow()
    }

    mongo.db.orders.insert_one(order)
    created_order = {k: v for k, v in order.items() if k != '_id'}
    return jsonify({
        "message": "Order created successfully",
        "order": created_order
    })


# =====================
# DASHBOARD API
# =====================

def _get_order_value(order):
    try:
        value = float(order.get('gross_value') or order.get('amount') or 0)
    except Exception:
        value = 0.0
    return value


def get_dashboard_metrics(user_id):
    total_products = mongo.db.products.count_documents({"farmer_id": user_id})
    total_orders = mongo.db.orders.count_documents({"farmer_id": user_id})

    shipped_query = {"farmer_id": user_id, "status": {"$regex": '^shipped$|^delivered$|^completed$', "$options": 'i'}}
    shipped_orders = list(mongo.db.orders.find(shipped_query, {"_id": 0}))

    earnings = sum(_get_order_value(o) for o in shipped_orders)

    farmer_name = ''
    try:
        farmer = mongo.db.users.find_one({"_id": ObjectId(user_id)})
        if farmer:
            farmer_name = farmer.get('name', '')
    except Exception:
        farmer_name = ''

    return {
        "farmer_name": farmer_name or 'Farmer',
        "total_products": total_products,
        "total_orders": total_orders,
        "earnings": round(earnings, 2),
        "shipped_orders": shipped_orders
    }


@app.route('/api/dashboard', methods=['GET'])
@jwt_required()
def dashboard_data():
    user_id = get_jwt_identity()
    data = get_dashboard_metrics(user_id)
    return jsonify({
        "farmer_name": data["farmer_name"],
        "total_products": data["total_products"],
        "total_orders": data["total_orders"],
        "earnings": data["earnings"]
    })


@app.route('/api/dashboard/report', methods=['GET'])
@jwt_required()
def download_dashboard_report():
    user_id = get_jwt_identity()
    format_type = request.args.get('format', 'pdf').lower()
    dashboard = get_dashboard_metrics(user_id)

    if format_type == 'csv':
        rows = []
        rows.append(['Farmer Name', dashboard['farmer_name']])
        rows.append(['Total Products', dashboard['total_products']])
        rows.append(['Total Orders', dashboard['total_orders']])
        rows.append(['Total Earnings', dashboard['earnings']])
        rows.append([])
        rows.append(['Order ID', 'Customer', 'Crop', 'Qty', 'Gross Value', 'Status', 'Created At'])
        for order in mongo.db.orders.find({"farmer_id": user_id}, {"_id": 0}):
            rows.append([
                order.get('order_id', ''),
                order.get('customer_name', ''),
                order.get('crop_name', ''),
                order.get('quantity', ''),
                order.get('gross_value', order.get('amount', '')),
                order.get('status', ''),
                str(order.get('created_at', ''))
            ])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(rows)
        csv_data = output.getvalue()
        output.close()

        return send_file(
            io.BytesIO(csv_data.encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name='kisansetu_dashboard_report.csv'
        )

    if format_type == 'pdf':
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib import colors
        except Exception:
            return jsonify({"message": "PDF generation library not available. Install reportlab."}), 500

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph('KisanSetu Farmer Report', styles['Title']))
        story.append(Spacer(1, 12))
        story.append(Paragraph(f'Farmer: {dashboard["farmer_name"]}', styles['Normal']))
        story.append(Paragraph(f'Date: {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}', styles['Normal']))
        story.append(Spacer(1, 12))

        summary_table = Table([
            ['Metric', 'Value'],
            ['Total Products', str(dashboard['total_products'])],
            ['Total Orders', str(dashboard['total_orders'])],
            ['Total Earnings', f'₹{dashboard["earnings"]:,.2f}']
        ], colWidths=[200, 200])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#164e29')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT')
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 18))

        orders = list(mongo.db.orders.find({"farmer_id": user_id}, {"_id": 0}))
        if orders:
            story.append(Paragraph('Order Summary', styles['Heading2']))
            order_rows = [['Order ID', 'Customer', 'Crop', 'Qty', 'Gross', 'Status']]
            for order in orders:
                order_rows.append([
                    order.get('order_id', ''),
                    order.get('customer_name', ''),
                    order.get('crop_name', ''),
                    str(order.get('quantity', '')),
                    f'₹{_get_order_value(order):,.2f}',
                    order.get('status', '')
                ])
            table = Table(order_rows, repeatRows=1, colWidths=[70, 90, 90, 40, 70, 80])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#164e29')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('ALIGN', (3, 1), (4, -1), 'RIGHT')
            ]))
            story.append(table)

        doc.build(story)
        buffer.seek(0)
        return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name='kisansetu_dashboard_report.pdf')

    return jsonify({"message": "Unsupported report format"}), 400


# Profile APIs
@app.route('/api/profile', methods=['GET'])
@jwt_required()
def get_profile():
    user_id = get_jwt_identity()
    try:
        user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        user = None

    if not user:
        return jsonify({}), 404

    profile = {
        "name": user.get('name', ''),
        "mobile": user.get('mobile', ''),
        "photo_url": user.get('photo_url', ''),
        "verified": user.get('verified', True)
    }
    return jsonify(profile)


@app.route('/api/profile', methods=['PUT'])
@jwt_required()
def update_profile():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    update = {}
    if 'name' in data:
        update['name'] = data['name']
    if 'photo_url' in data:
        update['photo_url'] = data['photo_url']
    if 'verified' in data:
        update['verified'] = bool(data['verified'])

    if update:
        mongo.db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})

    # return updated profile
    user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
    profile = {
        "name": user.get('name', ''),
        "mobile": user.get('mobile', ''),
        "photo_url": user.get('photo_url', ''),
        "verified": user.get('verified', True)
    }
    return jsonify(profile)


@app.route('/api/earnings', methods=['GET'])
@jwt_required()
def earnings_data():
    user_id = get_jwt_identity()

    # Query for shipped orders (case-insensitive match)
    shipped_query = {"farmer_id": user_id, "status": {"$regex": '^shipped$', "$options": 'i'}}
    shipped_orders = list(mongo.db.orders.find(shipped_query, {"_id": 0}))

    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    tomorrow = today_start + timedelta(days=1)
    month_start = datetime(now.year, now.month, 1)

    total = 0.0
    today_total = 0.0
    month_total = 0.0

    # Prepare monthly buckets for current year (Jan..Dec)
    monthly = [0.0] * 12

    for o in shipped_orders:
        gv = float(o.get('gross_value', 0) or 0)
        total += gv

        shipped_at = o.get('shipped_at') or o.get('created_at')
        if isinstance(shipped_at, str):
            try:
                shipped_at = datetime.fromisoformat(shipped_at)
            except Exception:
                shipped_at = None

        if shipped_at:
            if shipped_at >= today_start and shipped_at < tomorrow:
                today_total += gv
            if shipped_at >= month_start and shipped_at.year == now.year and shipped_at.month == now.month:
                month_total += gv
            if shipped_at.year == now.year:
                monthly[shipped_at.month - 1] += gv

    # Round values to 2 decimals
    def r(v):
        return round(v, 2)

    return jsonify({
        "total": r(total),
        "today": r(today_total),
        "month": r(month_total),
        "monthly": [r(x) for x in monthly],
        "orders": shipped_orders
    })


@app.route('/api/earnings/export', methods=['GET'])
@jwt_required()
def export_tax_statement():
    user_id = get_jwt_identity()

    # Fetch earnings data
    resp = earnings_data()
    data = resp.get_json() if hasattr(resp, 'get_json') else resp

    # Find farmer name if available
    farmer_name = None
    try:
        farmer = mongo.db.users.find_one({"_id": ObjectId(user_id)})
        if farmer:
            farmer_name = farmer.get('name')
    except Exception:
        farmer_name = None

    if not farmer_name:
        farmer_name = 'Farmer'

    # Create PDF
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib import colors
    except Exception:
        return jsonify({"message": "PDF generation library not available. Install reportlab."}), 500

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    c.setFont('Helvetica-Bold', 16)
    c.drawString(40, height - 60, 'KisanSetu - Tax Statement')
    c.setFont('Helvetica', 10)
    c.drawString(40, height - 80, f'Farmer: {farmer_name}')
    c.drawString(40, height - 95, f'Date Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}')

    # Use 'Rs' in PDF to avoid missing glyphs in default PDF fonts
    c.drawString(40, height - 120, f'Total Net Earnings: Rs {data.get("total", 0):,.2f}')

    # Monthly breakdown
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, height - 150, 'Monthly Breakdown (Current Year)')
    c.setFont('Helvetica', 10)
    y = height - 170
    months = [calendar.month_name[i] for i in range(1, 13)]
    monthly = data.get('monthly', [])
    for i, m in enumerate(months):
        amt = monthly[i] if i < len(monthly) else 0
        c.drawString(50, y, f'{m}: ₹{amt:,.2f}')
        y -= 14
        if y < 80:
            c.showPage()
            y = height - 60

    # Orders table header
    c.setFont('Helvetica-Bold', 12)
    c.drawString(40, y - 10, 'Shipped Orders')
    y -= 30
    c.setFont('Helvetica-Bold', 9)
    c.drawString(40, y, 'Order ID')
    c.drawString(120, y, 'Customer')
    c.drawString(240, y, 'Crop')
    c.drawString(340, y, 'Qty')
    c.drawString(390, y, 'Gross (₹)')
    c.drawString(470, y, 'Shipped At')
    y -= 14
    c.setFont('Helvetica', 9)

    for o in data.get('orders', []):
        if y < 60:
            c.showPage()
            y = height - 60
        shipped_at = o.get('shipped_at') or o.get('created_at')
        if isinstance(shipped_at, datetime):
            sh = shipped_at.strftime('%Y-%m-%d')
        else:
            try:
                sh = str(shipped_at)[:10] if shipped_at is not None else ''
            except Exception:
                sh = ''

        c.drawString(40, y, str(o.get('order_id', '')))
        c.drawString(120, y, str(o.get('customer_name', ''))[:18])
        c.drawString(240, y, str(o.get('crop_name', ''))[:18])
        c.drawString(340, y, str(o.get('quantity', '')))
        c.drawRightString(440, y, f'₹{float(o.get("gross_value",0)) :,.2f}')
        c.drawString(470, y, sh)
        y -= 14

    c.showPage()
    c.save()
    buffer.seek(0)

    return send_file(buffer, mimetype='application/pdf', as_attachment=True, download_name='kisansetu_tax_statement.pdf')


# ---------------------
# Update Order Status
# ---------------------
@app.route('/api/orders/<order_id>/status', methods=['PUT'])
@jwt_required()
def update_order_status(order_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    new_status = data.get('status', 'Shipped')

    set_fields = {"status": new_status}
    if str(new_status).lower() == 'shipped':
        set_fields['shipped_at'] = datetime.utcnow()

    result = mongo.db.orders.update_one(
        {"farmer_id": user_id, "order_id": order_id},
        {"$set": set_fields}
    )

    if result.matched_count == 0:
        return jsonify({"message": "Order not found"}), 404

    updated = mongo.db.orders.find_one({"farmer_id": user_id, "order_id": order_id}, {"_id": 0})
    return jsonify({"message": "Order status updated", "order": updated})


# =====================
# FRONTEND ROUTES (UNCHANGED)
# =====================

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/register')
def register():
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/products')
def products():
    return render_template('my-products.html')

@app.route('/orders')
def orders():
    return render_template('orders.html')

@app.route('/earnings')
def earnings():
    return render_template('earnings.html')

@app.route('/profile')
def profile():
    return render_template('profile.html')

@app.route('/logout')
def logout():
    return redirect(url_for('login'))

# =====================
# TEST MONGODB
# =====================

@app.route('/test')
def test():
    mongo.db.test.insert_one({"msg": "hello"})
    return "MongoDB Working"


# =====================
# RUN SERVER
# =====================

if __name__ == '__main__':
    app.run(debug=True, port=5000)