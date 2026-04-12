from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timedelta
from sqlalchemy import Float, or_
import pandas as pd
import numpy as np
import re
import os
import json
import chardet
import requests
from statsmodels.tsa.holtwinters import ExponentialSmoothing

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# 全局存储type.csv营养标准（完全适配你的中文列名）
TYPE_NUTRITION_STANDARD = {}

# ====================== 模型定义 ======================
class Food(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), index=True)
    food_code = db.Column(db.String(10), index=True)
    data_json = db.Column(db.Text)
    energy = db.Column(db.Float, default=0.0)
    protein = db.Column(db.Float, default=0.0)
    fat = db.Column(db.Float, default=0.0)
    carbs = db.Column(db.Float, default=0.0)
    sodium = db.Column(db.Float, default=0.0)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password = db.Column(db.String(50))
    gender = db.Column(db.String(10), default='男')
    age_start = db.Column(db.Float, default=18.0)
    age_end = db.Column(db.Float, default=29.0)
    pal = db.Column(db.Integer, default=1)

class UserFood(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    food_id = db.Column(db.Integer, db.ForeignKey('food.id'), nullable=False)
    name = db.Column(db.String(200))
    weight = db.Column(Float, default=100.0)
    meal = db.Column(db.String(50))
    date = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ====================== 前端页面路由 ======================
@app.route('/')
def index():
    return render_template('index.html')

# ====================== 工具函数 ======================
def detect_encoding(file_path):
    with open(file_path, 'rb') as f:
        raw = f.read(10000)
    return chardet.detect(raw)['encoding'] or 'gbk'

def clean_nutrition_value(val):
    if pd.isna(val) or val is None:
        return 0.0
    s = str(val).strip().replace(' ', '').replace(',', '.')
    if s in ('-', '', '—', 'NA', '无', '微量', 'nan', 'NaN'):
        return 0.0
    match = re.search(r'(\d+\.?\d*)', s)
    return float(match.group(1)) if match else 0.0

# ====================== 加载type.csv（完全适配你的中文列名） ======================
def load_type_csv():
    global TYPE_NUTRITION_STANDARD
    csv_path = 'type.csv'
    if not os.path.exists(csv_path):
        print("⚠️ 未找到type.csv，使用默认营养标准")
        return

    # 检测编码（完美兼容中文CSV）
    encoding = detect_encoding(csv_path)
    try:
        df = pd.read_csv(csv_path, encoding=encoding, low_memory=False)
    except:
        try:
            df = pd.read_csv(csv_path, encoding='gbk', low_memory=False)
        except:
            df = pd.read_csv(csv_path, encoding='utf-8', low_memory=False)
            print("⚠️ type.csv编码异常，使用utf-8读取")

    # 直接使用你的中文列名，无需转换
    required_cols = ['性别', '年龄段_start', '年龄段_end', 'PAL', '能量', '蛋白质', '脂肪', '碳水', '钠']
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        print(f"❌ type.csv缺少必要列：{missing}，使用默认标准")
        return

    # 存储为区间字典（key=(性别, PAL), value=区间列表）
    TYPE_NUTRITION_STANDARD = {}
    for _, row in df.iterrows():
        try:
            gender = str(row['性别']).strip()
            start = float(row['年龄段_start'])
            end = float(row['年龄段_end'])
            pal = int(row['PAL'])
            
            # 提取营养值
            standard = {
                "energy": clean_nutrition_value(row['能量']),
                "protein": clean_nutrition_value(row['蛋白质']),
                "fat": clean_nutrition_value(row['脂肪']),
                "carbs": clean_nutrition_value(row['碳水']),
                "sodium": clean_nutrition_value(row['钠'])
            }

            # 按性别+PAL分组存储区间
            key = (gender, pal)
            if key not in TYPE_NUTRITION_STANDARD:
                TYPE_NUTRITION_STANDARD[key] = []
            TYPE_NUTRITION_STANDARD[key].append({
                "start": start,
                "end": end,
                "standard": standard
            })
        except Exception as e:
            print(f"⚠️ type.csv行数据异常：{e}，跳过")
            continue

    print(f"✅ 成功加载type.csv，共{len(TYPE_NUTRITION_STANDARD)}组分类标准")

# ====================== 匹配用户营养标准（左闭右开区间匹配） ======================
def get_user_nutrition_standard(gender, age_start, age_end, pal):
    global TYPE_NUTRITION_STANDARD
    # 1. 优先匹配用户性别+PAL
    key = (gender, pal)
    if key not in TYPE_NUTRITION_STANDARD:
        # 匹配失败，用同PAL的默认性别（男）
        key = ('男', pal)
        if key not in TYPE_NUTRITION_STANDARD:
            print(f"⚠️ 未找到{gender}/PAL{pal}的标准，使用默认值")
            return {"energy":1800,"protein":55,"fat":60,"carbs":300,"sodium":2000}

    # 2. 区间匹配（左闭右开：用户年龄 ∈ [start, end)）
    for item in TYPE_NUTRITION_STANDARD[key]:
        if item['start'] <= age_start and age_end <= item['end']:
            return item['standard']

    # 3. 区间匹配失败，用同分类的默认区间
    print(f"⚠️ 未找到{gender}/{age_start}-{age_end}岁/PAL{pal}的区间，使用默认值")
    return {"energy":1800,"protein":55,"fat":60,"carbs":300,"sodium":2000}

# ====================== 初始化食物数据 ======================
def init_food():
    try:
        db.session.query(Food).delete()
        db.session.commit()
        print("🗑️ 已清空旧食材数据")
    except:
        db.session.rollback()

    csv_path = 'food.csv'
    if not os.path.exists(csv_path):
        print("⚠️ 未找到food.csv")
        return

    encoding = detect_encoding(csv_path)
    try:
        df = pd.read_csv(csv_path, encoding=encoding, low_memory=False)
    except:
        try:
            df = pd.read_csv(csv_path, encoding='gbk', low_memory=False)
        except:
            df = pd.read_csv(csv_path, encoding='utf-8', low_memory=False)

    success = 0
    for _, row in df.iterrows():
        try:
            name = None
            for c in df.columns:
                if '食物名称' in str(c) or '食品名称' in str(c):
                    name = str(row[c]).strip()
                    break
            # ========== 格式化食物编码：6位 / 7位（x结尾） ==========
            food_code = "000000"
            for c in df.columns:
                if '食物编码' in str(c) or '编码' in str(c):
                    raw_code = str(row[c]).strip().lower()
                    # 清洗：只保留数字 + x
                    clean_code = ''.join([ch for ch in raw_code if ch.isdigit() or ch == 'x'])
                    
                    # 格式化规则
                    if clean_code.endswith('x'):
                        # 7位：最后是x，前面补0
                        food_code = clean_code.zfill(7)
                    else:
                        # 6位：数字，前面补0
                        food_code = clean_code.zfill(6)
                    break
            if not name or name.lower() == 'nan':
                continue

            food_data = {}
            for col in df.columns:
                val = row[col]
                food_data[str(col)] = str(val) if pd.notna(val) else ""

            energy = 0.0
            protein = 0.0
            fat = 0.0
            carbs = 0.0
            sodium = 0.0

            for c in df.columns:
                cn = str(c).strip()
                v = clean_nutrition_value(row[c])
                if '能量' in cn or '热量' in cn:
                    energy = v
                elif '蛋白质' in cn:
                    protein = v
                elif '脂肪' in cn:
                    fat = v
                elif '碳水' in cn:
                    carbs = v
                elif '钠' in cn:
                    sodium = v

            food = Food(
                name=name,
                food_code=food_code, 
                energy=energy,
                protein=protein,
                fat=fat,
                carbs=carbs,
                sodium=sodium,
                data_json=json.dumps({
                    **food_data, 
                    "食物编码": food_code
                }, ensure_ascii=False)
            )
            db.session.add(food)
            success += 1
        except Exception as e:
            continue

    db.session.commit()
    print(f"✅ 成功导入{success}条食材数据")

# ====================== 评分与耦合协调度 ======================
def calculate_score(actual, target):
    weights = {"energy":0.25,"protein":0.20,"fat":0.20,"carbs":0.20,"sodium":0.15}
    score = 0
    for k in weights:
        if target[k] <= 0:
            continue
        dev = abs(actual[k] - target[k]) / target[k]
        score += max(0, (1 - dev) * 100 * weights[k])
    return round(score, 2)

def coupling_coordination(U1, U2):
    if (U1 + U2) == 0:
        return 0, 0, 0, "无数据"
    C = 2 * np.sqrt(U1 * U2) / (U1 + U2)
    T = 0.5 * U1 + 0.5 * U2
    D = np.sqrt(C * T)
    if D >= 8.0:
        judge = "优质协调"
    elif D >= 6.0:
        judge = "良好协调"
    elif D >= 4.0:
        judge = "基本协调"
    else:
        judge = "不协调"
    return round(C,4), round(T,4), round(D,4), judge

# ====================== 登录注册 ======================
@app.route('/register', methods=['POST'])
def register():
    try:
        d = request.json
        if not d.get('username') or not d.get('password'):
            return jsonify({"code":0,"msg":"账号或密码不能为空"})
        if User.query.filter_by(username=d['username']).first():
            return jsonify({"code":0,"msg":"账号已存在"})
        
        # 解析分类数据
        gender = d.get('gender', '男')
        ageRange = d.get('ageRange', '18,29').split(',')
        age_start = float(ageRange[0])
        age_end = float(ageRange[1])
        pal = int(d.get('pal', 1))

        u = User(
            username=d['username'],
            password=d['password'],
            gender=gender,
            age_start=age_start,
            age_end=age_end,
            pal=pal
        )
        db.session.add(u)
        db.session.commit()
        return jsonify({"code":1,"msg":"注册成功"})
    except Exception as e:
        print(e)
        return jsonify({"code":0,"msg":"注册失败"})

@app.route('/login', methods=['POST'])
def login():
    try:
        d = request.json
        u = User.query.filter_by(username=d['username'], password=d['password']).first()
        if u:
            return jsonify({
                "code":1,
                "user_id":u.id,
                "username":u.username,
                "gender":u.gender,
                "ageRange":f"{u.age_start},{u.age_end}",
                "pal":u.pal
            })
        else:
            return jsonify({"code":0,"msg":"账号或密码错误"})
    except:
        return jsonify({"code":0,"msg":"登录失败"})

# ====================== 个人资料修改 ======================
@app.route('/update_profile', methods=['POST'])
def update_profile():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        user = User.query.get(user_id)
        if not user:
            return jsonify({"code":0,"msg":"用户不存在"})
        
        # 更新密码（非空才改）
        password = data.get('password')
        if password and password.strip():
            user.password = password
        
        # 更新分类数据
        gender = data.get('gender')
        if gender:
            user.gender = gender
        
        ageRange = data.get('ageRange')
        if ageRange:
            start, end = ageRange.split(',')
            user.age_start = float(start)
            user.age_end = float(end)
        
        pal = data.get('pal')
        if pal:
            user.pal = int(pal)
        
        db.session.commit()
        return jsonify({"code":1,"msg":"保存成功"})
    except Exception as e:
        print(e)
        return jsonify({"code":0,"msg":"修改失败"})

# ====================== 食材操作 ======================
@app.route('/search_food', methods=['POST'])
def search_food():
    kw = str(request.json.get('keyword', '')).strip()
    user_id = request.json.get('user_id')

    HIDE_FOOD_IDS = list(range(1590, 1782))
    hide_restricted = True  # 默认：屏蔽 8-99

    # =========================
    # 【修复】严格按你原来逻辑：只有 0~0.5 / 0.5~1 / 1~3 才不屏蔽
    # =========================
    if user_id:
        user = User.query.get(user_id)
        if user:
            a = user.age_start
            b = user.age_end

            # 你的原版判断条件（我完全保留，只修复判断逻辑）
            if (a == 0.0 and b == 0.5) or \
               (a == 0.5 and b == 1.0) or \
               (a == 1.0 and b == 3.0):
                hide_restricted = False  # 宝宝：不屏蔽

    # 搜索（完全不变）
    query = Food.query.filter(
        or_(
            Food.name.like(f"%{kw}%"),
            Food.food_code.like(f"%{kw}%")
        )
    )

    # =========================
    # 【修复】稳定屏蔽：非 0-3 岁一定屏蔽 8-99
    # =========================
    if hide_restricted:
        query = query.filter(~Food.id.in_(HIDE_FOOD_IDS))

    foods = query.limit(30).all()

    res = []
    for f in foods:
        res.append({
            "id": f.id,
            "name": f.name,
            "code": f.food_code,
            "energy": f.energy,
            "protein": f.protein,
            "fat": f.fat,
            "carbs": f.carbs,
            "sodium": f.sodium
        })

    return jsonify({"data": res})

@app.route('/save_user_food', methods=['POST'])
def save_user_food():
    d = request.json
    uf = UserFood(
        user_id=d['user_id'],
        food_id=d['food_id'],
        name=d['name'],
        weight=d.get('weight',100),
        meal=d['meal'],
        date=d['date']
    )
    db.session.add(uf)
    db.session.commit()
    return jsonify({"code":1,"msg":"保存成功"})

@app.route('/get_user_foods', methods=['POST'])
def get_user_foods():
    d = request.json
    user_foods = UserFood.query.filter_by(user_id=d['user_id'], date=d['date']).all()
    res = []
    for uf in user_foods:
        food = Food.query.get(uf.food_id)
        if food:
            res.append({
                "id": uf.id, "food_id": uf.food_id, "name": uf.name,
                "weight": uf.weight, "meal": uf.meal,
                # ✅ 关键修复：把完整的data_json返回给前端！
                "data_json": food.data_json,
                "foodData": {"id": food.id, "name": food.name, "energy": food.energy, "protein": food.protein, "fat": food.fat, "carbs": food.carbs, "sodium": food.sodium}
            })
    return jsonify({"code":1,"data":res})

@app.route('/delete_user_food', methods=['POST'])
def delete_user_food():
    uf = UserFood.query.get(request.json.get('id'))
    if uf:
        db.session.delete(uf)
        db.session.commit()
    return jsonify({"code":1,"msg":"删除成功"})

# ====================== 百度识图（后端安全调用） ======================

@app.route("/recognize_food", methods=["POST"])
def recognize_food():
    try:
        # 填写你的百度密钥
        API_KEY = "AU5JXPQruK1N28NsHpK6NQbW"
        SECRET_KEY = "amQQGOLch6IFLdwSNpSsAHnXwUPzp8ms"

        # 获取 token（官方标准）
        token_url = "https://aip.baidubce.com/oauth/2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": API_KEY,
            "client_secret": SECRET_KEY
        }
        token_resp = requests.post(token_url, data=data, timeout=10)
        token_json = token_resp.json()
        access_token = token_json.get("access_token")

        if not access_token:
            return jsonify({"code":0,"msg":"token获取失败"})

        # 图片
        image = request.json.get("image")

        # 官方接口地址
        url = "https://aip.baidubce.com/rest/2.0/image-classify/v2/advanced_general"
        post_data = {
            "image": image,
            "baike_num": 0
        }

        resp = requests.post(
            url,
            params={"access_token": access_token},
            data=post_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10
        )

        result = resp.json()
        raw = result.get("result", [])

        final = []
        for item in raw:
            score = float(item.get("score", 0))
            keyword = item.get("keyword", "").strip()

            if score < 0.2:
                continue

            # 匹配食材库
            food = Food.query.filter(
                or_(Food.name.like(f"%{keyword}%"), Food.food_code.like(f"%{keyword}%"))
            ).first()

            if food:
                final.append({
                    "name": food.name,
                    "food_id": food.id,
                    "score": round(score,2)
                })
            else:
                final.append({
                    "name": keyword + "（未匹配）",
                    "food_id": None,
                    "score": round(score,2)
                })

        return jsonify({"code":1,"data":final[:5]})

    except Exception as e:
        return jsonify({"code":0,"msg":str(e)})
        
# ====================== 拖拽更新餐次 ======================
@app.route('/update_food_meal', methods=['POST'])
def update_food_meal():
    d = request.json
    uf_id = d.get('id')
    new_meal = d.get('meal')
    
    uf = UserFood.query.get(uf_id)
    if uf:
        uf.meal = new_meal
        db.session.commit()
        return jsonify({"code":1})
    return jsonify({"code":0})

@app.route('/clear_user_foods', methods=['POST'])
def clear_user_foods():
    d = request.json
    UserFood.query.filter_by(user_id=d['user_id'], date=d['date']).delete()
    db.session.commit()
    return jsonify({"code":1,"msg":"清空成功"})

# ====================== 营养汇总 ======================
@app.route('/get_total_nutri', methods=['POST'])
def get_total_nutri():
    try:
        foods = request.json.get('foods', [])
        user_id = request.json.get('user_id')
        e=p=f=c=s=0.0

        # 计算实际摄入
        for it in foods:
            w = float(it.get('weight',100))/100
            fd = it.get('foodData',{})
            e += float(fd.get('energy',0)) * w
            p += float(fd.get('protein',0)) * w
            f += float(fd.get('fat',0)) * w
            c += float(fd.get('carbs',0)) * w
            s += float(fd.get('sodium',0)) * w
        e,p,f,c,s = round(e,1),round(p,1),round(f,1),round(c,1),round(s,1)

        # 获取用户专属标准
        user = User.query.get(user_id) if user_id else None
        if user:
            targets = get_user_nutrition_standard(
                gender=user.gender,
                age_start=user.age_start,
                age_end=user.age_end,
                pal=user.pal
            )
        else:
            targets = {"energy":1800,"protein":55,"fat":60,"carbs":300,"sodium":2000}

        # 计算评分
        actual = {"energy":e,"protein":p,"fat":f,"carbs":c,"sodium":s}
        score = calculate_score(actual, targets)
        U2 = calculate_score(targets, targets)
        C,T,D,judge = coupling_coordination(score, U2)

        return jsonify({
            **actual,
            "score":score, "C":C, "T":T, "D":D, "judge":judge,
            "target_energy": targets["energy"],
            "target_protein": targets["protein"],
            "target_fat": targets["fat"],
            "target_carbs": targets["carbs"],
            "target_sodium": targets["sodium"]
        })
    except Exception as e:
        print(e)
        return jsonify({
            "energy":0,"protein":0,"fat":0,"carbs":0,"sodium":0,
            "score":0,"C":0,"T":0,"D":0,"judge":"异常",
            "target_energy":1800,"target_protein":55,"target_fat":60,"target_carbs":300,"target_sodium":2000
        })

# ====================== 营养预测 ======================
@app.route('/predict_nutrition', methods=['POST'])
def predict_nutrition():
    try:
        user_id = request.json.get('user_id')
        user = User.query.get(user_id)
        if user:
            recommended = get_user_nutrition_standard(
                gender=user.gender,
                age_start=user.age_start,
                age_end=user.age_end,
                pal=user.pal
            )
        else:
            recommended = {"energy":1800,"protein":55,"fat":60,"carbs":300,"sodium":2000}

        records = UserFood.query.filter_by(user_id=user_id).all()
        day_set = {r.date for r in records}
        real_days = sorted(list(day_set))
        real_count = len(real_days)

        if real_count == 0:
            return jsonify({
                "status": "no_data",
                "days": [], "energy": [], "protein": [], "fat": [], "carbs": [], "sodium": [],
                "gap_energy": 0, "gap_protein": 0, "gap_fat": 0, "gap_carbs": 0, "gap_sodium": 0,
                **{f"target_{k}":v for k,v in recommended.items()}
            })

        end_dt = datetime.now()
        dates = pd.date_range(end=end_dt, periods=60, freq='D')
        history = {k: [] for k in recommended}

        for k, base in recommended.items():
            for i in range(60):
                wd = i % 7
                seasonal = 1.05 if wd >= 5 else 0.98
                noise = np.random.normal(1, 0.02)
                history[k].append(base * seasonal * noise)

        for d_str in real_days:
            items = UserFood.query.filter_by(user_id=user_id, date=d_str).all()
            e = p = f = c = s = 0.0
            for it in items:
                food = Food.query.get(it.food_id)
                if not food: continue
                r = it.weight / 100
                e += food.energy * r
                p += food.protein * r
                f += food.fat * r
                c += food.carbs * r
                s += food.sodium * r
            dt = datetime.strptime(d_str, '%Y-%m-%d')
            diff_days = (end_dt - dt).days
            pos = 59 - diff_days
            if 0 <= pos < 60:
                history["energy"][pos] = e
                history["protein"][pos] = p
                history["fat"][pos] = f
                history["carbs"][pos] = c
                history["sodium"][pos] = s

        df = pd.DataFrame(history, index=dates)
        pred = {}
        for nutri in recommended:
            pred[nutri] = holt_winters_forecast(df[nutri]).tolist()

        gap_energy  = round(recommended["energy"]  - np.mean(pred["energy"]),  1)
        gap_protein = round(recommended["protein"] - np.mean(pred["protein"]), 1)
        gap_fat     = round(recommended["fat"]     - np.mean(pred["fat"]),    1)
        gap_carbs   = round(recommended["carbs"]   - np.mean(pred["carbs"]),  1)
        gap_sodium  = round(recommended["sodium"]  - np.mean(pred["sodium"]), 1)

        status = "partial" if real_count < 7 else "full"

        return jsonify({
            "status": status,
            "days": [f"{i+1}天后" for i in range(7)],
            "energy": [round(v, 1) for v in pred["energy"]],
            "protein": [round(v, 1) for v in pred["protein"]],
            "fat": [round(v, 1) for v in pred["fat"]],
            "carbs": [round(v, 1) for v in pred["carbs"]],
            "sodium": [round(v, 1) for v in pred["sodium"]],
            "gap_energy": gap_energy,
            "gap_protein": gap_protein,
            "gap_fat": gap_fat,
            "gap_carbs": gap_carbs,
            "gap_sodium": gap_sodium,
            **{f"target_{k}":v for k,v in recommended.items()}
        })
    except Exception as e:
        print("Predict Error:", e)
        return jsonify({"status": "error"})

def holt_winters_forecast(series, forecast_days=7, seasonal_periods=7):
    model = ExponentialSmoothing(
        series, trend='add', seasonal='add',
        seasonal_periods=seasonal_periods, freq='D'
    )
    fitted_model = model.fit(smoothing_level=0.2, smoothing_trend=0.1, smoothing_seasonal=0.1)
    return fitted_model.forecast(steps=forecast_days)

# ====================== 智能推荐与多因子分析 ======================
@app.route('/get_advice_data', methods=['POST'])
def get_advice_data():
    try:
        data = request.json
        user_id = data.get('user_id')
        foods = data.get('foods', [])
        user = User.query.get(user_id)

        # 1. 计算当前摄入
        e = p = f = c = s = 0.0
        food_contrib = []
        for it in foods:
            w = float(it.get('weight', 100)) / 100
            fd = it.get('foodData', {})
            ne = float(fd.get('energy', 0)) * w
            np = float(fd.get('protein', 0)) * w
            nf = float(fd.get('fat', 0)) * w
            nc = float(fd.get('carbs', 0)) * w
            ns = float(fd.get('sodium', 0)) * w
            e += ne
            p += np
            f += nf
            c += nc
            s += ns
            food_contrib.append({
                "name": it['name'],
                "weight": it['weight'],
                "energy": ne,
                "protein": np,
                "fat": nf,
                "carbs": nc,
                "sodium": ns
            })

        # 2. 获取推荐标准
        if user:
            target = get_user_nutrition_standard(user.gender, user.age_start, user.age_end, user.pal)
        else:
            target = {"energy": 1800, "protein": 55, "fat": 60, "carbs": 300, "sodium": 2000}

        # 3. 计算缺口 & 偏差百分比
        gap = {
            "energy": round(target["energy"] - e, 1),
            "protein": round(target["protein"] - p, 1),
            "fat": round(target["fat"] - f, 1),
            "carbs": round(target["carbs"] - c, 1),
            "sodium": round(target["sodium"] - s, 1)
        }

        pct = {
            "energy": (gap["energy"] / target["energy"]) * 100 if target["energy"] != 0 else 0,
            "protein": (gap["protein"] / target["protein"]) * 100 if target["protein"] != 0 else 0,
            "fat": (gap["fat"] / target["fat"]) * 100 if target["fat"] != 0 else 0,
            "carbs": (gap["carbs"] / target["carbs"]) * 100 if target["carbs"] != 0 else 0,
            "sodium": (gap["sodium"] / target["sodium"]) * 100 if target["sodium"] != 0 else 0
        }

        # 4. 多因子贡献度计算（完全不变）
        weights = {"energy": 0.3, "protein": 0.2, "fat": 0.2, "carbs": 0.2, "sodium": 0.1}
        total_e = sum([x["energy"] for x in food_contrib]) or 1
        total_p = sum([x["protein"] for x in food_contrib]) or 1
        total_f = sum([x["fat"] for x in food_contrib]) or 1
        total_c = sum([x["carbs"] for x in food_contrib]) or 1
        total_s = sum([x["sodium"] for x in food_contrib]) or 1

        for fc in food_contrib:
            fc["energy_pct"] = fc["energy"] / total_e * 100
            fc["protein_pct"] = fc["protein"] / total_p * 100
            fc["fat_pct"] = fc["fat"] / total_f * 100
            fc["carbs_pct"] = fc["carbs"] / total_c * 100
            fc["sodium_pct"] = fc["sodium"] / total_s * 100
            fc["score"] = (
                fc["energy_pct"] * weights["energy"] +
                fc["protein_pct"] * weights["protein"] +
                fc["fat_pct"] * weights["fat"] +
                fc["carbs_pct"] * weights["carbs"] +
                fc["sodium_pct"] * weights["sodium"]
            )

        food_contrib = sorted(food_contrib, key=lambda x: x["score"], reverse=True)

        # ====================== 食材屏蔽（完全同步搜索页） ======================
        HIDE_FOOD_IDS = list(range(1590, 1782))
        hide_restricted = True
        if user:
            a = user.age_start
            b = user.age_end
            if (a == 0.0 and b == 0.5) or (a == 0.5 and b == 1.0) or (a == 1.0 and b == 3.0):
                hide_restricted = False

        all_foods_query = Food.query
        if hide_restricted:
            all_foods_query = all_foods_query.filter(~Food.id.in_(HIDE_FOOD_IDS))
        all_foods = all_foods_query.all()

        food_list = []
        for fd in all_foods:
            food_list.append({
                "name": fd.name,
                "energy": fd.energy,
                "protein": fd.protein,
                "fat": fd.fat,
                "carbs": fd.carbs,
                "sodium": fd.sodium
            })

        # ====================== 【核心修复1】排序逻辑：只显示≥20%，按绝对值从大到小 ======================
        priority_list = []
        # 蛋白质：只要绝对值≥20%，就生成推荐（缺口>0=需要补充）
        if abs(pct["protein"]) >= 20:
            priority_list.append( (abs(pct["protein"]), "protein", True) )
        # 热量
        if abs(pct["energy"]) >= 20:
            priority_list.append( (abs(pct["energy"]), "energy", True) )
        # 脂肪
        if abs(pct["fat"]) >= 20:
            priority_list.append( (abs(pct["fat"]), "fat", True) )
        # 碳水
        if abs(pct["carbs"]) >= 20:
            priority_list.append( (abs(pct["carbs"]), "carbs", True) )
        # 钠：只要绝对值≥20%，就生成推荐（缺口<0=超标，推荐低钠）
        if abs(pct["sodium"]) >= 20:
            priority_list.append( (abs(pct["sodium"]), "sodium", False) )

        # 按绝对值从大到小排序（保证蛋白质第一）
        priority_list = sorted(priority_list, key=lambda x: x[0], reverse=True)

        # ====================== 【核心修复2】生成推荐 ======================
        rec = {}
        for abs_pct, key, is_supplement in priority_list:
            if key == "protein":
                rec["protein"] = sorted(food_list, key=lambda x: x["protein"], reverse=True)[:6]
            elif key == "energy":
                rec["energy"] = sorted(food_list, key=lambda x: x["energy"], reverse=True)[:6]
            elif key == "fat":
                rec["fat"] = sorted(food_list, key=lambda x: x["fat"], reverse=True)[:6]
            elif key == "carbs":
                rec["carbs"] = sorted(food_list, key=lambda x: x["carbs"], reverse=True)[:6]
            elif key == "sodium":
                rec["sodium"] = sorted(food_list, key=lambda x: x["sodium"])[:6]

        return jsonify({
            "current": {"energy": round(e,1), "protein": round(p,1), "fat": round(f,1), "carbs": round(c,1), "sodium": round(s,1)},
            "target": target,
            "gap": gap,
            "pct": pct,
            "foods": food_contrib,
            "recommend": rec
        })
    except Exception as e:
        print("Advice Error:", e)
        return jsonify({"code": 0})

# ====================== 首页智能推荐TOP3 ======================
@app.route('/get_index_recommend', methods=['POST'])
def get_index_recommend():
    try:
        user_id = request.json.get('user_id')
        all_foods = Food.query.all()
        data = []
        for f in all_foods:
            data.append({
                "name": f.name,
                "energy": round(f.energy,1),
                "protein": round(f.protein,1),
                "fat": round(f.fat,1),
                "carbs": round(f.carbs,1),
                "sodium": round(f.sodium,1)
            })

        top_energy = sorted(data, key=lambda x: x["energy"], reverse=True)[:3]
        top_protein = sorted(data, key=lambda x: x["protein"], reverse=True)[:3]
        top_fat = sorted(data, key=lambda x: x["fat"], reverse=True)[:3]
        top_carbs = sorted(data, key=lambda x: x["carbs"], reverse=True)[:3]

        return jsonify({
            "energy": top_energy,
            "protein": top_protein,
            "fat": top_fat,
            "carbs": top_carbs
        })
    except:
        return jsonify({"code":0})
    
# ====================== 启动 ======================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        load_type_csv()  # 启动时加载type.csv
        init_food()
    app.run(debug=True, host='0.0.0.0', port=5000)
