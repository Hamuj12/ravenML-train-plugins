import tensorflow as tf
import glob
import os
import json
from .model import preprocess_image


def recursive_map_dict(d, f):
    if isinstance(d, dict):
        return {k: recursive_map_dict(v, f) for k, v in d.items()}
    return f(d)


def dataset_from_directory(dir_path, crop_size, nb_keypoints=None, focal_length=None, object_name="cygnus"):
    """
    Get a Tensorflow dataset that generates samples from a directory with test data
    that is not in TFRecord format (i.e. a directory with image_*.png and meta_*.json files).
    The images are cropped to the spacecraft using the bounding box truth data.
    :param dir_path: the path to the directory
    :param crop_size: the output size for the images, in pixels
    :return: a Tensorflow dataset that generates (image, metadata) tuples where image is a [cropsize, cropsize, 3]
    Tensor and metadata is a dictionary of Tensors.
    """

    def parse(metadata, img_id):
        parsed = {
            "bbox": metadata["bboxes"][object_name],
            "pose": metadata["pose"],
            "translation": metadata["translation"],
            "image_id": img_id,
        }
        if focal_length is None:
            parsed["focal_length"] = metadata["focal_length"]
        else:
            parsed["focal_length"] = focal_length
        if "keypoints" in metadata:
            parsed["keypoints"] = metadata["keypoints"]
        return recursive_map_dict(parsed, tf.convert_to_tensor)

    def generator():
        image_files = sorted(glob.glob(os.path.join(dir_path, "image_*")))
        meta_files = sorted(glob.glob(os.path.join(dir_path, "meta_*.json")))
        for image_file, meta_file in zip(image_files, meta_files):
            # load metadata
            with open(meta_file, "r") as f:
                metadata = json.load(f)
            img_id = os.path.basename(image_file).split("_")[1].split(".")[0]
            if not metadata["bboxes"]:
                continue
            if not metadata["bboxes"].get(object_name):
                continue
            metadata = parse(metadata, img_id)
            yield image_file, metadata

    meta_file_0 = glob.glob(os.path.join(dir_path, "meta_*.json"))[0]
    with open(meta_file_0, "r") as f:
        meta0 = json.load(f)
    dtypes = recursive_map_dict(parse(meta0, "0"), lambda x: x.dtype)
    dataset = tf.data.Dataset.from_generator(generator, (tf.string, dtypes))

    def process(image_file, metadata):
        # load bounding box
        bbox = metadata["bbox"]
        xmin = bbox["xmin"]
        xmax = bbox["xmax"]
        ymin = bbox["ymin"]
        ymax = bbox["ymax"]
        centroid = tf.convert_to_tensor(
            [(ymax + ymin) / 2, (xmax + xmin) / 2], dtype=tf.float32
        )
        bbox_size = tf.cast(tf.maximum(xmax - xmin, ymax - ymin), tf.float32) * 1.25

        # load and crop image
        image_data = tf.io.read_file(image_file)
        imdims, image = preprocess_image(image_data, centroid, bbox_size, crop_size)

        truth = {
            "pose": tf.ensure_shape(metadata["pose"], [4]),
            "bbox_size": tf.ensure_shape(bbox_size, []),
            "centroid": tf.ensure_shape(centroid, [2]),
            "imdims": imdims,
            "position": tf.ensure_shape(metadata["translation"], [3]),
            "image_id": metadata["image_id"],
        }
        if "focal_length" in metadata:
            truth["focal_length"] = tf.ensure_shape(metadata["focal_length"], [])
        if nb_keypoints and "keypoints" in metadata:
            truth["keypoints"] = (
                tf.cast(metadata["keypoints"][:nb_keypoints], tf.float32) * imdims
            )
        return image, truth

    return dataset.map(process)
