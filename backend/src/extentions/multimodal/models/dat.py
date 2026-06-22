import os
import time
import urllib.request
import numpy as np
import cv2
import onnxruntime
from io import BytesIO
from typing import List


class OrientationDetector:
    def __init__(self, model_path = "src/extentions/multimodal/models/model.onnx"):
        self.model_path = model_path
        self.input_shape = (512, 512)
        self.mean = np.array((0.694, 0.695, 0.693), dtype=np.float32)
        self.std = np.array((0.299, 0.296, 0.301), dtype=np.float32)
        self.classes = [0, 270, 180, 90]

        sess_options = onnxruntime.SessionOptions()
        sess_options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL

        # providers = [('CUDAExecutionProvider',
        #               {'device_id': 0, 'cudnn_conv_algo_search': 'DEFAULT', 'do_copy_in_default_stream': True}),
        #              'CPUExecutionProvider']
        providers = []
        self.session = onnxruntime.InferenceSession(self.model_path, providers=providers, sess_options=sess_options)
        self.input_name = self.session.get_inputs()[0].name


    def _preprocess(self, image_bytes_list: List[BytesIO]) -> np.ndarray:
        batch_images = []
        target_h, target_w = self.input_shape
        total_files = len(image_bytes_list)

        for idx, img_bytes in enumerate(image_bytes_list):
            t_start = time.time()
            img_bytes.seek(0)
            file_bytes = np.asarray(bytearray(img_bytes.read()), dtype=np.uint8)
            img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

            h, w = img.shape[:2]
            scale = min(target_w / w, target_h / h)
            new_w, new_h = int(w * scale), int(h * scale)
            resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

            delta_w = target_w - new_w
            delta_h = target_h - new_h
            top, bottom = delta_h // 2, delta_h - (delta_h // 2)
            left, right = delta_w // 2, delta_w - (delta_w // 2)

            padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=[0, 0, 0])
            normalized = (padded.astype(np.float32) / 255.0 - self.mean) / self.std
            batch_images.append(normalized.transpose(2, 0, 1))
            t_end = time.time()

        return np.stack(batch_images, axis=0)

    def get_orientations_from_images_batch(self, image_bytes_list: List[BytesIO]) -> List[float]:
        if not image_bytes_list:
            return []
        input_tensor = self._preprocess(image_bytes_list)
        logits = self.session.run(None, {self.input_name: input_tensor})[0]
        max_idxs = np.argmax(logits, axis=1)
        return [self.classes[idx] for idx in max_idxs]

    def get_orientation_from_image(self, image_bytes: BytesIO) -> float:
        return self.get_orientations_from_images_batch([image_bytes])[0]

    def get_all_orientations_from_pdf(self, pdf: BytesIO) -> List[float]:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            raise ImportError("Please install pypdfium2 to process PDFs: pip install pypdfium2")

        pdf.seek(0)
        pdf_doc = pdfium.PdfDocument(pdf)
        images = []
        print(f"Extracting {len(pdf_doc)} pages from PDF...")
        for i in range(len(pdf_doc)):
            t0 = time.time()
            page = pdf_doc[i]
            bitmap = page.render(scale=2)
            pil_image = bitmap.to_pil()
            img_byte_arr = BytesIO()
            pil_image.save(img_byte_arr, format='JPEG')
            images.append(img_byte_arr)
            print(f"Page {i + 1} extracted: {time.time() - t0:.4f}s")

        return self.get_orientations_from_images_batch(images)


if __name__ == "__main__":
    print("Initializing detector...")
    detector = OrientationDetector()

    image_paths = ["tests/files_output/screenshot_1769570338.png"]

    image_bytes_list = []
    for path in image_paths:
        with open(path, "rb") as f:
            image_bytes_list.append(BytesIO(f.read()))

    print("\nStarting batch orientation detection...")
    start_t = time.time()
    orientations = detector.get_orientations_from_images_batch(image_bytes_list)
    print(f"Total execution time: {time.time() - start_t:.4f}s")
    print(f"Batch image orientations: {orientations}")