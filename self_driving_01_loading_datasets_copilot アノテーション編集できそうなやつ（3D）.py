from nuscenes import NuScenes
import fiftyone as fo
from nuscenes.utils.geometry_utils import box_in_image, view_points, BoxVisibility
import numpy as np
from nuscenes.scripts.export_poses import derive_latlon
from nuscenes.utils.data_io import load_bin_file
from nuscenes.utils.color_map import get_colormap
from nuscenes.lidarseg.lidarseg_utils import paint_points_label
from nuscenes.utils.data_classes import LidarPointCloud, RadarPointCloud
import open3d as o3d
import os
from pyquaternion import Quaternion
from PIL import Image

os.environ.setdefault("VFF_EXP_ANNOTATION", "1")

def _get_instance(instance_map, box, fallback_key):
    instance_key = (
        getattr(box, "instance_token", None)
        or getattr(box, "token", None)
        or fallback_key
    )
    instance = instance_map.get(instance_key)
    if instance is None:
        instance = fo.Instance()
        instance_map[instance_key] = instance
    return instance


def camera_sample(group, filepath, sensor, token, scene, instance_map):
    if group is None:
        sample = fo.Sample(filepath=filepath)
    else:
        sample = fo.Sample(filepath=filepath, group=group.element(sensor))
    data_path, boxes, camera_intrinsic = nusc.get_sample_data(token, box_vis_level=BoxVisibility.NONE,)
    data = nusc.get('sample_data', token)
    cs_record = nusc.get('calibrated_sensor', data["calibrated_sensor_token"])
    pose_record = nusc.get('ego_pose', data["ego_pose_token"])
    image = Image.open(data_path)
    width, height = image.size
    shape = (height,width)
    polylines = []
    detections = []
    log = nusc.get('log', scene["log_token"])
    location = log["location"]
    ego = nusc.get('ego_pose', data["ego_pose_token"])
    ego_list = [ego]

    latlon = derive_latlon(location,ego_list)
    lat = latlon[0]["latitude"]
    lon = latlon[0]["longitude"]
    sample["location"] = fo.GeoLocation(
        point = [lon,lat]
    )
    sample["nusc_camera_intrinsic"] = camera_intrinsic.tolist()
    sample["nusc_sensor_rotation"] = cs_record["rotation"]
    sample["nusc_sensor_translation"] = cs_record["translation"]
    sample["nusc_ego_rotation"] = pose_record["rotation"]
    sample["nusc_ego_translation"] = pose_record["translation"]
    sample["nusc_image_width"] = width
    sample["nusc_image_height"] = height
    for box_index, box in enumerate(boxes):
        if box_in_image(box,camera_intrinsic,shape,vis_level=BoxVisibility.ALL):
            c = np.array(nusc.colormap[box.name]) / 255.0
            corners = view_points(box.corners(), camera_intrinsic, normalize=True)[:2, :]
            x_coords = corners[0] / width
            y_coords = corners[1] / height
            x_min = max(0.0, float(x_coords.min()))
            x_max = min(1.0, float(x_coords.max()))
            y_min = max(0.0, float(y_coords.min()))
            y_max = min(1.0, float(y_coords.max()))
            box_w = max(0.0, x_max - x_min)
            box_h = max(0.0, y_max - y_min)
            bottom = [(corners[0][0]/width,corners[1][0]/height),
                      (corners[0][1]/width,corners[1][1]/height),
                      (corners[0][5]/width,corners[1][5]/height),
                      (corners[0][4]/width,corners[1][4]/height),]
            instance = _get_instance(instance_map, box, f"camera-{box_index}")
            detections.append(
                fo.Detection(
                    label=box.name,
                    bounding_box=[x_min, y_min, box_w, box_h],
                    instance=instance,
                )
            )
            polylines.append(
                fo.Polyline(
                    label=box.name,
                    points=[bottom],
                    closed=True,
                    filled=False,
                    instance=instance,
                )
            )
    sample["detections"] = fo.Detections(detections=detections)
    sample["cuboids"] = fo.Polylines(polylines=polylines)
    return sample

def load_lidar(lidar_token):

    #Grab and Generate Colormaps
    gt_from = "lidarseg"
    lidarseg_filename = dataroot + nusc.get(gt_from, lidar_token)['filename']
    colormap = get_colormap()
    name2index = nusc.lidarseg_name2idx_mapping

    coloring = paint_points_label(lidarseg_filename,None,name2index, colormap=colormap)
    filepath = dataroot + nusc.get("sample_data", lidar_token)['filename']
    root, extension = os.path.splitext(filepath)
    if extension.lower() == ".bin" and root.lower().endswith(".pcd"):
        pcd_path = os.path.abspath(root)
    elif extension.lower() == ".pcd":
        pcd_path = os.path.abspath(filepath)
    else:
        pcd_path = os.path.abspath(root + ".pcd")

    #Load Point Cloud
    cloud = LidarPointCloud.from_file(filepath)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(cloud.points[:3,:].T)
    colors = coloring[:,:3]
    colors.max()
    pcd.colors = o3d.utility.Vector3dVector(colors)

    #Save back Point Cloud
    o3d.io.write_point_cloud(pcd_path, pcd)

    return pcd_path


def build_fo3d_scene(pcd_path):
    scene = fo.Scene()
    abs_pcd_path = os.path.abspath(pcd_path)
    pcd_filename = os.path.basename(abs_pcd_path)
    scene.add(fo.PointCloud("lidar", pcd_filename))
    fo3d_path = os.path.splitext(abs_pcd_path)[0] + ".fo3d"
    scene.write(fo3d_path)
    return fo3d_path

def lidar_sample(group, filepath, sensor, lidar_token, scene, instance_map):
    # Get the lidar data
    data_path, boxes, camera_intrinsic = nusc.get_sample_data(lidar_token, box_vis_level=BoxVisibility.NONE,)
    data = nusc.get('sample_data', lidar_token)
    cs_record = nusc.get('calibrated_sensor', data["calibrated_sensor_token"])
    pose_record = nusc.get('ego_pose', data["ego_pose_token"])
    log = nusc.get('log', scene["log_token"])
    location = log["location"]
    ego = nusc.get('ego_pose', data["ego_pose_token"])
    ego_list = [ego]
    latlon = derive_latlon(location,ego_list)
    lat = latlon[0]["latitude"]
    lon = latlon[0]["longitude"]

    # Create a sample
    if group is None:
        sample = fo.Sample(filepath=filepath)
    else:
        sample = fo.Sample(filepath=filepath, group=group.element(sensor))

    # Add the coords to the sample
    sample["location"] = fo.GeoLocation(
        point = [lon,lat]
    )
    sample["nusc_sensor_rotation"] = cs_record["rotation"]
    sample["nusc_sensor_translation"] = cs_record["translation"]
    sample["nusc_ego_rotation"] = pose_record["rotation"]
    sample["nusc_ego_translation"] = pose_record["translation"]

    # Add detections to the pcd
#    detections = []
    polylines = []
    for box_index, box in enumerate(boxes):

        x, y, z = box.orientation.yaw_pitch_roll
        w, l, h = box.wlh.tolist()

        instance = _get_instance(instance_map, box, f"lidar-{box_index}")
#        detection = fo.Detection(
#                label=box.name,
#                location=box.center.tolist(),
#                rotation=[z, y, x],
#                dimensions=[l,w,h],
#                instance=instance,
#                )
#        detections.append(detection)
        corners_3d = box.corners().T
        front = corners_3d[:4]
        back = corners_3d[4:]
        top = corners_3d[[3, 2, 6, 7], :]
        bottom = corners_3d[[0, 1, 5, 4], :]
        polyline = fo.Polyline(
            label=box.name,
            points=[],
            closed=True,
            filled=False,
            instance=instance,
        )
        polyline.points3d = [
            bottom.tolist(),
        ]
        polylines.append(polyline)
#    sample["ground_truth"] = fo.Detections(detections=detections)
    sample["cuboids"] = fo.Polylines(polylines=polylines)
    return sample


def load_radar(filepath, data ):

    root, extension = os.path.splitext(filepath)

    #Load Point Cloud
    pc = RadarPointCloud.from_file(filepath)

    cs_record = nusc.get('calibrated_sensor', data['calibrated_sensor_token'])
    pc.rotate(Quaternion(cs_record['rotation']).rotation_matrix)
    pc.translate(np.array(cs_record['translation']))

    pcd = o3d.geometry.PointCloud()
    print(pc.points.shape)
    pcd.points = o3d.utility.Vector3dVector(pc.points[:3,:].T)

    #Save back Point Cloud
    o3d.io.write_point_cloud(root+"_NEW.pcd", pcd)

    return root+"_NEW.pcd"



# Make sure its absolute path!
dataroot='dataset/'
nusc = NuScenes(version='v1.0-mini', dataroot=dataroot, verbose=True)

# New dataset for all sensors including LIDAR and RADAR
all_sensor_dataset = fo.Dataset("nuscenes_sensors2",overwrite=True)
all_sensor_dataset.add_group_field("group", default="CAM_FRONT")

annotation_dataset = fo.Dataset("nuscenes_annotations", overwrite=True)
lidar_annotation_dataset = fo.Dataset("nuscenes_lidar_annotations", overwrite=True)

groups = ["CAM_FRONT", "CAM_FRONT_RIGHT", "CAM_BACK_RIGHT", "CAM_BACK",
           "CAM_BACK_LEFT", "CAM_FRONT_LEFT","LIDAR_TOP"]

samples = []
annotation_samples = []
lidar_annotation_samples = []

# Iterate over each scene
for scene in nusc.scene:
    my_scene = scene
    token = my_scene['first_sample_token']
    my_sample = nusc.get('sample', token)
    last_sample_token = my_scene['last_sample_token']

    # Iterate over each sample in the scene
    sample_count = 0
    while not my_sample["next"] == "":
        if sample_count >= 2:  # 各シーンから最初の2サンプルだけ処理
            break
        sample_count += 1
        scene_token = my_sample["scene_token"]
        lidar_token = my_sample["data"]["LIDAR_TOP"]
        group = fo.Group()
        instance_map = {}
        # Iterate over each sensor in the sample
        for sensor in groups:
            data = nusc.get('sample_data', my_sample['data'][sensor])
            filepath = dataroot + data["filename"]

            # Check if the sensor is lidar
            if data["sensor_modality"] == "lidar":
                filepath = load_lidar(lidar_token)
                sample = lidar_sample(group,filepath, sensor, lidar_token, scene, instance_map)
                fo3d_path = build_fo3d_scene(filepath)
                lidar_annotation_samples.append(
                    lidar_sample(
                        None,
                        fo3d_path,
                        sensor,
                        lidar_token,
                        scene,
                        instance_map,
                    )
                )

            # Check if the sensor is camera
            elif data["sensor_modality"] == "camera":
                sample = camera_sample(group, filepath, sensor, my_sample['data'][sensor],scene, instance_map)
                annotation_samples.append(
                    camera_sample(
                        None,
                        filepath,
                        sensor,
                        my_sample['data'][sensor],
                        scene,
                        instance_map,
                    )
                )

            # Else its radar
            else:
                radar_filepath = load_radar(filepath,data)
                sample = fo.Sample(filepath=radar_filepath, group=group.element(sensor))


            # Add metadata to the sample
            sample["token"] = data["token"]
            sample["ego_pose_token"] = data["ego_pose_token"]
            sample["calibrated_sensor_token"] = data["calibrated_sensor_token"]
            sample["timestamp"] = data["timestamp"]
            sample["is_key_frame"] = data["is_key_frame"]
            sample["prev"] = data["prev"]
            sample["next"] = data["next"]
            sample["scene_token"] = scene_token


            samples.append(sample)

        token = my_sample["next"]

        my_sample = nusc.get('sample', token)

# Add the samples to the dataset, group by scene_token, and launch the app
all_sensor_dataset.add_samples(samples)
annotation_dataset.add_samples(annotation_samples)
lidar_annotation_dataset.add_samples(lidar_annotation_samples)

annotation_dataset.set_label_schemas(
    annotation_dataset.generate_label_schemas(
        fields=["detections"],
        scan_samples=True,
    )
)
annotation_dataset.activate_label_schemas()

lidar_annotation_dataset.set_label_schemas(
    lidar_annotation_dataset.generate_label_schemas(
        fields=["cuboids"],
        scan_samples=True,
    )
)
lidar_annotation_dataset.activate_label_schemas()

view = lidar_annotation_dataset
session = fo.launch_app(view)
session.wait()
