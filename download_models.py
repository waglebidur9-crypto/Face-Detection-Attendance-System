import os
import requests

MODELS = {
    "face_detection_yunet_2023mar.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
}

def download_models():
    for name, url in MODELS.items():
        if not os.path.exists(name):
            print(f"Downloading {name}...")
            r = requests.get(url, allow_redirects=True)
            with open(name, 'wb') as f:
                f.write(r.content)
            print(f"Downloaded {name} successfully.")
        else:
            print(f"{name} already exists.")

if __name__ == "__main__":
    download_models()