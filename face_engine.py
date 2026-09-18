import os
import cv2
import numpy as np

class FaceEngine:
    def __init__(self, yunet_path=None, sface_path=None):
        # Resolve path relative to current script directory to prevent FileNotFoundError
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        if yunet_path is None:
            yunet_path = os.path.join(base_dir, "models", "face_detection_yunet_2023mar.onnx")
        if sface_path is None:
            sface_path = os.path.join(base_dir, "models", "face_recognition_sface_2021dec.onnx")

        if not os.path.exists(yunet_path) or not os.path.exists(sface_path):
            raise FileNotFoundError(
                f"\n[ERROR] Missing ONNX model files!\n"
                f"Expected locations:\n - Detector: {yunet_path}\n - Recognizer: {sface_path}\n"
                f"Please ensure a 'models' folder exists with both ONNX files."
            )

        # Initialize YuNet Face Detector
        self.detector = cv2.FaceDetectorYN.create(
            model=yunet_path,
            config="",
            input_size=(320, 320),
            score_threshold=0.6,
            nms_threshold=0.3,
            top_k=5000
        )

        # Initialize SFace Recognizer
        self.recognizer = cv2.FaceRecognizerSF.create(
            model=sface_path,
            config=""
        )

    def detect_and_align(self, img):
        if img is None:
            return None
        
        height, width, _ = img.shape
        self.detector.setInputSize((width, height))
        
        _, faces = self.detector.detect(img)
        return faces

    def extract_features(self, img, face_data):
        aligned_face = self.recognizer.alignCrop(img, face_data)
        feature = self.recognizer.feature(aligned_face)
        return feature

    def match_face(self, query_feature, known_faces, threshold=0.363):
        """
        Compares facial features against registered database encodings.
        Calibrated to 0.363 cosine similarity for standard webcam environments.
        """
        if not known_faces or query_feature is None:
            return None, 0.0

        best_match = None
        best_score = -1.0

        for student in known_faces:
            stored_encoding = student["encoding"]
            
            # Reshape array to (1, 128) if stored as 1D array
            if stored_encoding.ndim == 1:
                stored_encoding = np.expand_dims(stored_encoding, axis=0)

            score = self.recognizer.match(query_feature, stored_encoding, cv2.FaceRecognizerSF_FR_COSINE)
            
            if score > best_score:
                best_score = score
                best_match = student

        if best_score >= threshold:
            return best_match, best_score

        return None, best_score