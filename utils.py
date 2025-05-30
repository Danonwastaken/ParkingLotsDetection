import cv2
import json
from ultralytics import YOLO
import math
import os
import streamlink
from openpyxl import Workbook
from io import BytesIO
import psycopg2
import time
from datetime import datetime

UPLOAD_FOLDER = "videos"
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'wav'}

model = YOLO("weights/best.pt")
output_dir = "resized_videos"
screenshot_dir = "static/images/lots"
parking_lots_dir = "parking_lots"
targetClasses = [2, 3]
iou_threshold = 0.85
dist = 0.5
parking_stats = {}
frame_data = {}  
current_frame = {}
last_time = {}

def get_db_connection():
    conn = psycopg2.connect(
        host='localhost',
        database='postgres',
        user='postgres',
        password='8210'
    )
    return conn

def calc_iou(p_box, c_box):
    p_x1, p_y1, p_x2, p_y2 = p_box
    c_x1, c_y1, c_x2, c_y2 = c_box

    x1_i = max(p_x1, c_x1)
    y1_i = max(p_y1, c_y1)
    x2_i = min(p_x2, c_x2)
    y2_i = min(p_y2, c_y2)

    intersection = max(0, x2_i - x1_i) * max(0, y2_i - y1_i)
    
    area1 = (p_x2 - p_x1) * (p_y2 - p_y1)
    area2 = (c_x2 - c_x1) * (c_y2 - c_y1)
    union = area1 + area2 - intersection
    
    if union > 0:
        iou = intersection / union 
    else:
        iou = 0
    return iou  

def calc_1(p_box, c_box):
    p_x1, p_y1, p_x2, p_y2 = p_box
    c_x1, c_y1, c_x2, c_y2 = c_box

    area1 = (p_x2 - p_x1) * (p_y2 - p_y1)
    area2 = (c_x2 - c_x1) * (c_y2 - c_y1)
    scale = max(area1, area2) / min(area1, area2)
    return scale

def distance(p_box, c_box):
    p_x1, p_y1, p_x2, p_y2 = p_box
    c_x1, c_y1, c_x2, c_y2 = c_box

    p_x_middle = (p_x1+p_x2)/2
    p_y_middle = (p_y1+p_y2)/2
    c_x_middle = (c_x1+c_x2)/2
    c_y_middle = (c_y1+c_y2)/2

    x_cords = p_x_middle - c_x_middle
    y_cords = p_y_middle - c_y_middle
    diag_lenght = math.sqrt(((c_x_middle - c_x1)**2) + ((c_y_middle - c_y1)**2)) + 0.00000001
    return diag_lenght / (math.sqrt(((x_cords)**2) + ((y_cords)**2)) + 0.00000001)

def save_db_c(title, is_stream, spot_path):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('INSERT INTO source (title, is_stream, spot_path)'
        'VALUES (%s, %s, %s)',
        (title,
        is_stream,
        spot_path)
        )
        conn.commit()

    except Exception as e:
        print(f"Ошибка. Не удалось сохранить данные для камеры {title}")

    cur.close()
    conn.close()


def save_db_v(camera, video_id, free_parking, no_parking, is_url, frame_number=None):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        current_date = datetime.now().date()
        if is_url:
                current_time = datetime.now().time().strftime("%H:%M:%S") 
                cur.execute(
                'INSERT INTO stream_data (video_id, date, time, free_space, occupied_space)'
                'VALUES (%s, %s, %s, %s, %s)',
                (video_id, current_date, current_time, free_parking, no_parking)
            )
        else:
            cur.execute(
                'INSERT INTO video_data (video_id, frame_number, date, free_space, occupied_space)'
                'VALUES (%s, %s, %s, %s, %s)',
                (video_id, frame_number, current_date, free_parking, no_parking)
            )
        conn.commit() 
    except Exception as e:
        print(f"Ошибка. Не удалось сохранить данные для камеры {camera}")

def generate_frames(camera):
    print(camera)
    global parking_stats
    if camera not in frame_data:
        frame_data[camera] = []
        current_frame[camera] = 0
        last_time[camera] = time.time()
        
    json_path = f"{parking_lots_dir}/{camera}_lots.json"
    video_path = f"{output_dir}/{camera}_resized.mp4"
    stream_json_path = f"{output_dir}/{camera}_stream.json"
    print(stream_json_path)
    try:
        with open(json_path, 'r') as file:
            rectangles = json.load(file)
    except FileNotFoundError:
        return 

    is_url = os.path.exists(stream_json_path)
    cap = None
    if is_url:
        try:
            with open(stream_json_path, 'r') as file:
                stream_data = json.load(file)
            stream_url = stream_data.get('stream_url')
            streams = streamlink.streams(stream_url)
            if not streams:
                return  
            stream_url = streams["best"].url
            print(stream_url)
            cap = cv2.VideoCapture(stream_url)
        except Exception:
            return 
    else:
        if not os.path.exists(video_path):
            return  
        cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        print(f"Failed to open stream: {stream_url}")
        return  

    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute(
        'SELECT video_id FROM source WHERE title = %s',
        (camera,)
    )
    
    video_id = cur.fetchone()
    if video_id:
        video_id = video_id[0]
    else:
        cur.close()
        conn.close()
        cap.release()
        return
    
    frame_data[camera] = []
    current_frame[camera] = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            if is_url:
                continue  
            break  
        if is_url:
            frame = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
        current_frame[camera] += 1
        display_frame = frame.copy()
        free_parking = 0
        no_parking = 0
        results = model(frame)
        car_bb = []
        for result in results[0].boxes.data.tolist():
            x1, y1, x2, y2, score, class_id = result
            if int(class_id) in targetClasses:
                car_bb.append([int(x1), int(y1), int(x2), int(y2)])

        for rect in rectangles:
            foundCar = False
            start_x = rect["start"]["ix"]
            start_y = rect["start"]["iy"]
            end_x = rect["end"]["x"]
            end_y = rect["end"]["y"]
            parking_bb = [start_x, start_y, end_x, end_y]
            max_iou = 0
            for car_b in car_bb:
                if not foundCar:
                    iou = calc_iou(parking_bb, car_b) * calc_1(parking_bb, car_b) * (distance(parking_bb, car_b) * dist)
                    max_iou = max(max_iou, iou)
                
            if max_iou > iou_threshold:
                color = (0, 0, 255) 
                no_parking += 1
                foundCar = True
            else:
                color = (0, 255, 0) 
                free_parking += 1
            cv2.rectangle(display_frame, (start_x, start_y), (end_x, end_y), color, 2)

        total_parking = no_parking + free_parking
        parking_stats[camera] = {'free': free_parking, 'total': total_parking}

        frame_data[camera].append({
            'frame': current_frame[camera],
            'free': free_parking,
            'occupied': no_parking
        })

        current_time = time.time()
        if current_time - last_time[camera] >= 1.0:
            save_db_v(camera, video_id, free_parking, no_parking, is_url, current_frame[camera])
            last_time[camera] = current_time
            
        ret, buffer = cv2.imencode('.jpg', display_frame)
        if not ret:
            continue
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        
    cur.close()
    conn.close() 
    cap.release()

def generate_single(camera):
    json_path = f"{parking_lots_dir}/{camera}_lots.json"
    video_path = f"{output_dir}/{camera}_resized.mp4"
    stream_json_path = f"{output_dir}/{camera}_stream.json"

    try:
        with open(json_path, 'r') as file:
            rectangles = json.load(file)
    except FileNotFoundError:
        return 

    is_url = os.path.exists(stream_json_path)
    cap = None
    try:
        if is_url:
            try:
                with open(stream_json_path, 'r') as file:
                    stream_data = json.load(file)
                stream_url = stream_data.get('stream_url')
                streams = streamlink.streams(stream_url)
                if not streams:
                    return  
                stream_url = streams["best"].url
                cap = cv2.VideoCapture(stream_url)
            except Exception:
                return 
        else:
            if not os.path.exists(video_path):
                return  
            cap = cv2.VideoCapture(video_path)

        if not cap.isOpened():
            return  
        
        ret, frame = cap.read()
        if not ret:
            return
        
        if is_url:
            frame = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            'SELECT video_id FROM source WHERE title = %s',
            (camera,)
        )
        
        video_id = cur.fetchone()
        if video_id:
            video_id = video_id[0]
        else:
            cur.close()
            conn.close()
            cap.release()
            return

        current_frame[camera] = 0
        free_parking = 0
        no_parking = 0
        results = model(frame)
        car_bb = []
        for result in results[0].boxes.data.tolist():
            x1, y1, x2, y2, score, class_id = result
            if int(class_id) in targetClasses:
                car_bb.append([int(x1), int(y1), int(x2), int(y2)])

        for rect in rectangles:
            foundCar = False
            start_x = rect["start"]["ix"]
            start_y = rect["start"]["iy"]
            end_x = rect["end"]["x"]
            end_y = rect["end"]["y"]
            parking_bb = [start_x, start_y, end_x, end_y]
            max_iou = 0
            for car_b in car_bb:
                if not foundCar:
                    iou = calc_iou(parking_bb, car_b) * calc_1(parking_bb, car_b) * (distance(parking_bb, car_b) * dist)
                    max_iou = max(max_iou, iou)
                
            if max_iou > iou_threshold:
                no_parking += 1
                foundCar = True
            else:
                free_parking += 1

        total_parking = no_parking + free_parking
        parking_stats[camera] = {'free': free_parking, 'total': total_parking}
        save_db_v(camera, video_id, free_parking, no_parking, is_url, current_frame[camera])


        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Камера {camera}: свободно {free_parking} из {total_parking} мест")
        
    except Exception as e:
        print(f"Ошибка при обработке камеры {camera}: {str(e)}")

    cur.close()
    conn.close() 
    cap.release()

def get_live_stats():
    stats = {}
    cameras = get_available_cameras()
    for camera in cameras:
        stats[camera] = parking_stats.get(camera, {'free': 0, 'total': 0})
    return stats

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_aspect_ratio(video):
    cap = cv2.VideoCapture(video)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width > height:
        new_width = 640
        new_height = 360
    else:
        new_width = 360
        new_height = 640
    cap.release()  
    return new_width, new_height

def get_available_cameras():
    return [f.split('_lots.json')[0] for f in os.listdir(parking_lots_dir) if f.endswith('_lots.json')]


def extract_frame(s_url, screenshot_dir):
    try:
        streams = streamlink.streams(s_url)
        if not streams:
            raise ValueError("Не найдена трансляция по данному url")
        stream_url = streams["best"].url
        cap = cv2.VideoCapture(stream_url)
        ret, frame = cap.read()
        if not ret:
            cap.release()
        resized_frame = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
        cv2.imwrite(screenshot_dir, resized_frame)
        cap.release()
        return resized_frame
    except Exception as e:
        return e

def create_report(camera, frame_data_list):
    wb = Workbook()
    ws = wb.active
    ws.title = f"Report {camera}"
    ws.append(["Кадр", "Свободно", "Занято"])
    for data in frame_data_list:
        ws.append([data['frame'], data['free'], data['occupied']])
    
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


