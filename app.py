from flask import Flask, render_template, Response, jsonify, send_file
import cv2
import json
from ultralytics import YOLO
from openpyxl import Workbook
from io import BytesIO
import math
app = Flask(__name__)

model = YOLO("weights/best.pt")
targetClasses = [2, 3]
iou_threshold = 0.9
dist = 20
parking_stats = {'free': 0, 'total': 0}
frame_data = []  
current_frame = 0


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
    return math.sqrt(((x_cords)**2) + ((y_cords)**2)) + 0.00000001

def generate_frames(camera):
    global current_frame, frame_data
    video_path = f"resized_videos/{camera}_resized.mp4"
    json_path = f"parking_lots/{camera}_lots.json"
    
    with open(json_path, 'r') as file:
        rectangles = json.load(file)
    
    while True:
        cap = cv2.VideoCapture(video_path)
        current_frame = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break  

            current_frame += 1
            display_frame = frame.copy()
            free_parking = 0
            no_parking = 0
            total_parking = 0

            results = model(frame)
            car_bb = []
            for result in results[0].boxes.data.tolist():
                x1, y1, x2, y2, score, class_id = result
                if int(class_id) in targetClasses:
                    car_bb.append([int(x1), int(y1), int(x2), int(y2)])

            for rect in rectangles:
                start_x = rect["start"]["ix"]
                start_y = rect["start"]["iy"]
                end_x = rect["end"]["x"]
                end_y = rect["end"]["y"]
                parking_bb = [start_x, start_y, end_x, end_y]
                max_iou = 0
                for car_b in car_bb:
                    iou = calc_iou(parking_bb, car_b) * calc_1(parking_bb, car_b) * (dist/distance(parking_bb, car_b))
                    max_iou = max(max_iou, iou)
                
                if max_iou > iou_threshold:
                    color = (0, 0, 255)
                    no_parking += 1
                else:
                    color = (0, 255, 0)
                    free_parking += 1
                cv2.rectangle(display_frame, (start_x, start_y), (end_x, end_y), color, 2)

            total_parking = no_parking + free_parking
            parking_stats['free'] = free_parking
            parking_stats['total'] = total_parking

            frame_data.append({
                'frame': current_frame,
                'free': free_parking,
                'occupied': no_parking
            })

            ret, buffer = cv2.imencode('.jpg', display_frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

        cap.release()





@app.route('/')
def main():
    return render_template('main.html')

@app.route('/camera1')
def cam1():
    global frame_data, current_frame
    frame_data = []  
    current_frame = 0
    return render_template('camera1.html', camera='1')

@app.route('/camera2')
def cam2():
    global frame_data, current_frame
    frame_data = []  
    current_frame = 0
    return render_template('camera2.html', camera='2')

@app.route('/camera3')
def cam3():
    global frame_data, current_frame
    frame_data = []  
    current_frame = 0
    return render_template('camera3.html', camera='3')

@app.route('/info')
def info():
    return render_template('info.html')

@app.route('/stats/<camera>')
def get_stats(camera):
    return jsonify(parking_stats)

@app.route('/video_feed/<camera>')
def video_feed(camera):
    return Response(generate_frames(camera), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/download_report/<camera>')
def download_report(camera):
    wb = Workbook()
    ws = wb.active
    ws.title = f"Parking Report Camera {camera}"
    
    ws.append(["Frame Number", "Free Spaces", "Occupied Spaces"])
    
    for data in frame_data:
        ws.append([data['frame'], data['free'], data['occupied']])
    
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    
    return send_file(
        output,
        download_name=f"parking_report_camera_{camera}.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

if __name__ == '__main__':
    app.run(debug = True)