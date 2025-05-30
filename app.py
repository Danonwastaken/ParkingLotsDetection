from flask import Flask, render_template, Response, jsonify, send_file, request , session
from urllib.parse import urlparse, parse_qs
import cv2
import json
import os
from werkzeug.utils import secure_filename
import threading
import time

from utils import (
    allowed_file, get_aspect_ratio, generate_frames, get_available_cameras,
    extract_frame, create_report, generate_single, get_live_stats, save_db_c, parking_stats, frame_data, output_dir, screenshot_dir
)
app = Flask(__name__)
latest_stats = {}
UPLOAD_FOLDER = "videos"
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'wav'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
stats_lock = threading.Lock()
app.secret_key = 'key-8210'

def periodic_camera_processing():
    global latest_stats
    while True:
        cameras = get_available_cameras()
        for camera in cameras:
            try:
                generate_single(camera)
            except Exception as e:
                print(f"Ошибка при обработке камеры {camera}: {str(e)}")
        
        with stats_lock:
            latest_stats = get_live_stats()
            print(f"Обновлена статистика: {latest_stats}")
        
        time.sleep(60)

@app.route('/uploadVideo', methods=['POST'])
def upload_video():
    if 'video' not in request.files:
        return jsonify({'success': False, 'message': 'Файл не обнаружен'})
    
    file = request.files['video']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Файл не выбран'})
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        new_width, new_height = get_aspect_ratio(filepath)
        cap = cv2.VideoCapture(filepath)
        fps = cap.get(cv2.CAP_PROP_FPS)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        output_filename = os.path.splitext(filename)[0] + '_resized.mp4'
        output_video_path = os.path.join(output_dir, output_filename)
        out = cv2.VideoWriter(output_video_path, fourcc, fps, (new_width, new_height))   
        index = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
        
            resized_frame = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
            out.write(resized_frame)
            if index == 0:
                screenshot_name =  os.path.splitext(filename)[0] + '_lots.jpg'
                screenshot_path = os.path.join(screenshot_dir, screenshot_name)  
                cv2.imwrite(screenshot_path, resized_frame)
            index += 1

        cap.release()
        out.release()

        session['is_stream'] = False
        session['title'] = os.path.splitext(filename)[0]

        return jsonify({'success': True, 'message': 'Файл успешно загружен', 'screenshot_name': screenshot_name})
    
    return jsonify({'success': False, 'message': 'Неверный тип файла'})

@app.route('/uploadStream', methods=['POST'])
def upload_stream():
    try:
        data = request.get_json()
        stream_url = data.get('url')        
        parsed_url = urlparse(stream_url)
        if 'youtube.com' in parsed_url.netloc:
            query_params = parse_qs(parsed_url.query)
            video_id = query_params.get('v', [None])[0]
        elif 'youtu.be' in parsed_url.netloc:
            video_id = parsed_url.path.lstrip('/')
        else:
            return jsonify({'success': False, 'message': 'Формат ссылки не поддержтвается'})

        if not video_id:
            return jsonify({'success': False, 'message': 'Не удалось взять id с url'})
        
        screenshot_name = f"{video_id}_lots.jpg"
        screenshot_path = os.path.join(screenshot_dir, screenshot_name)
        extract_frame(stream_url, screenshot_path)
        json_data = {
            'stream_url': stream_url,
            'screenshot': screenshot_name
        }
        json_filename = f"{video_id}_stream.json"
        json_filepath = os.path.join(output_dir, json_filename)
        with open(json_filepath, 'w') as f:
            json.dump(json_data, f, indent=4)

        session['is_stream'] = True
        session['title'] = video_id

        return jsonify({'success': True, 'message': 'Url поток успешно загружен', 'screenshot_name': screenshot_name})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/saveRectangles', methods=['POST'])
def save_rectangles():
    try:
        data = request.json
        filename = data['filename']
        rectangles_data = data['data']
        is_stream = session.get('is_stream', False)
        title = session.get('title')
        filepath = os.path.join('parking_lots', filename)
        with open(filepath, 'w') as f:
            json.dump(rectangles_data, f, indent=4)

        save_db_c(title, is_stream, filepath)     
        return jsonify({'success': True, 'message': 'Файл успешно сохранён'})
    
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/')
def main():
    cameras = get_available_cameras()
    return render_template('main.html', cameras=cameras, stats=latest_stats)

@app.route('/camera/<camera_id>')
def camera(camera_id):
    if camera_id not in get_available_cameras():
        return render_template('error.html', message="Камера не найдена"), 404
    
    return render_template('camera.html', camera=camera_id)

@app.route('/info')
def info():
    return render_template('info.html')

@app.route('/addCamera')
def addCamera():
    return render_template('addCamera.html')

@app.route('/markerCam')
def markerCam():
    screenshot_name = request.args.get('screenshot_name')
    return render_template('markerCam.html', screenshot_name=screenshot_name)

@app.route('/get_stats')
def get_updated_stats():
    return jsonify(latest_stats)

@app.route('/stats/<camera>')
def get_stats(camera):
    stats = parking_stats.get(camera, {'free':0, 'total': 0})
    return jsonify(stats)

@app.route('/videoFeed/<camera>')
def video_feed(camera):
    return Response(generate_frames(camera), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/downloadReport/<camera>')
def download_report(camera):
    output = create_report(camera, frame_data.get(camera, []))
    return send_file(
        output,
        download_name=f"report_{camera}.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

if __name__ == '__main__':
    if not hasattr(app, 'background_thread'):
        app.background_thread = threading.Thread(target=periodic_camera_processing, daemon=True)
        app.background_thread.start()
    app.run(debug = False)