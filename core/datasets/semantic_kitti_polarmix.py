import os
import os.path

import numpy as np
from torchsparse import SparseTensor
from torchsparse.utils.collate import sparse_collate_fn
from torchsparse.utils.quantize import sparse_quantize
from torchpack.utils.logging import logger

from core.datasets.utils import polarmix

__all__ = ['SemanticKITTI_PolarMix']

label_name_mapping = {
    0: 'unlabeled',
    1: 'outlier',
    10: 'car',
    11: 'bicycle',
    13: 'bus',
    15: 'motorcycle',
    16: 'on-rails',
    18: 'truck',
    20: 'other-vehicle',
    30: 'person',
    31: 'bicyclist',
    32: 'motorcyclist',
    40: 'road',
    44: 'parking',
    48: 'sidewalk',
    49: 'other-ground',
    50: 'building',
    51: 'fence',
    52: 'other-structure',
    60: 'lane-marking',
    70: 'vegetation',
    71: 'trunk',
    72: 'terrain',
    80: 'pole',
    81: 'traffic-sign',
    99: 'other-object',
    252: 'moving-car',
    253: 'moving-bicyclist',
    254: 'moving-person',
    255: 'moving-motorcyclist',
    256: 'moving-on-rails',
    257: 'moving-bus',
    258: 'moving-truck',
    259: 'moving-other-vehicle'
}

kept_labels = [
    'road', 'sidewalk', 'parking', 'other-ground', 'building', 'car', 'truck',
    'bicycle', 'motorcycle', 'other-vehicle', 'vegetation', 'trunk', 'terrain',
    'person', 'bicyclist', 'motorcyclist', 'fence', 'pole', 'traffic-sign'
]

"""
{'car': 0, 'bicycle': 1, 'motorcycle': 2, 'truck': 3, 'other-vehicle': 4, 'person': 5, 'bicyclist': 6, 'motorcyclist': 7, 
'road': 8, 'parking': 9, 'sidewalk': 10, 'other-ground': 11, 'building': 12, 'fence': 13, 'vegetation': 14, 'trunk': 15, 
'terrain': 16, 'pole': 17, 'traffic-sign': 18}
"""
instance_classes = [0, 1, 2, 3, 4, 5, 6, 7]
Omega = [np.random.random() * np.pi * 2 / 3, (np.random.random() + 1) * np.pi * 2 / 3]  # x3


class SemanticKITTI_PolarMix(dict):

    def __init__(self, root, voxel_size, num_points, **kwargs):
        submit_to_server = kwargs.get('submit', False)
        sample_stride = kwargs.get('sample_stride', 1)
        google_mode = kwargs.get('google_mode', False)

        logger.info("SemanticKITTI with PolarMix\n")

        if submit_to_server:
            super().__init__({
                'train':
                    SemanticKITTIInternal(root,
                                          voxel_size,
                                          num_points,
                                          sample_stride=1,
                                          split='train',
                                          submit=True),
                'test':
                    SemanticKITTIInternal(root,
                                          voxel_size,
                                          num_points,
                                          sample_stride=1,
                                          split='test')
            })
        else:
            super().__init__({
                'train':
                    SemanticKITTIInternal(root,
                                          voxel_size,
                                          num_points,
                                          polarcutmix=True,
                                          sample_stride=1,
                                          split='train',
                                          google_mode=google_mode),
                'test':
                    SemanticKITTIInternal(root,
                                          voxel_size,
                                          num_points,
                                          sample_stride=sample_stride,
                                          split='val')
            })


class SemanticKITTIInternal:

    def __init__(self,
                 root,
                 voxel_size,
                 num_points,
                 split,
                 polarcutmix=False,
                 sample_stride=1,
                 submit=False,
                 google_mode=True):
        if submit:
            trainval = True
        else:
            trainval = False
        self.root = root
        self.split = split
        self.voxel_size = voxel_size
        self.num_points = num_points
        self.polarcutmix = polarcutmix
        self.sample_stride = sample_stride
        self.google_mode = google_mode
        self.seqs = []
        if split == 'train':
            self.seqs = ['00', 
                         '01', '02', '03', '04', '05', '06', '07', '09', '10'
                         ]
            if self.google_mode or trainval:
                self.seqs.append('08') # updated
        elif self.split == 'val':
            self.seqs = ['08']
        elif self.split == 'test':
            self.seqs = [
                        '11', 
                        '12', '13', '14', '15', '16', '17', '18', '19', '20', '21'
                        ]

        self.files = []
        for seq in self.seqs:
            seq_files = sorted(
                os.listdir(os.path.join(self.root, seq, 'velodyne')))
            seq_files = [
                os.path.join(self.root, seq, 'velodyne', x) for x in seq_files
            ]
            self.files.extend(seq_files)

        if self.sample_stride > 1:
            self.files = self.files[::self.sample_stride]

        reverse_label_name_mapping = {}
        self.label_map = np.zeros(260)
        cnt = 0
        for label_id in label_name_mapping:
            if label_id > 250:
                if label_name_mapping[label_id].replace('moving-',
                                                        '') in kept_labels:
                    self.label_map[label_id] = reverse_label_name_mapping[
                        label_name_mapping[label_id].replace('moving-', '')]
                else:
                    self.label_map[label_id] = 255
            elif label_id == 0:
                self.label_map[label_id] = 255
            else:
                if label_name_mapping[label_id] in kept_labels:
                    self.label_map[label_id] = cnt
                    reverse_label_name_mapping[
                        label_name_mapping[label_id]] = cnt
                    cnt += 1
                else:
                    self.label_map[label_id] = 255

        self.reverse_label_name_mapping = reverse_label_name_mapping
        self.num_classes = cnt
        self.angle = 0.0

    def set_angle(self, angle):
        self.angle = angle

    def __len__(self):
        return len(self.files)

    def read_lidar_scan(self, index):
        with open(self.files[index], 'rb') as b:
            block_ = np.fromfile(b, dtype=np.float32).reshape(-1, 4)
        label_file = self.files[index].replace('velodyne', 'labels').replace('.bin', '.label')
        if os.path.exists(label_file):
            with open(label_file, 'rb') as a:
                all_labels = np.fromfile(a, dtype=np.int32).reshape(-1)
        else:
            all_labels = np.zeros(block_.shape[0]).astype(np.int32)
        labels_ = self.label_map[all_labels & 0xFFFF].astype(np.int64)
        return block_, labels_

    def __getitem__(self, index):
        block_, labels_ = self.read_lidar_scan(index)

        if self.split == 'train':
            # read another lidar scan
            index_2 = np.random.randint(len(self.files))
            pts2, labels2 = self.read_lidar_scan(index_2)
            # polarmix
            alpha = (np.random.random() - 1) * np.pi
            beta = alpha + np.pi
            scale = np.random.uniform(0.90, 1.10)
            block_, labels_ = polarmix(block_, labels_, pts2, labels2,
                                      alpha=alpha, beta=beta,
                                      instance_classes=instance_classes,
                                      Omega=Omega, scale = scale)

        block = np.zeros_like(block_)

        if 'train' in self.split:
            theta = np.random.uniform(0, 2 * np.pi)
            scale_factor = np.random.uniform(0.95, 1.05)
            rot_mat = np.array([[np.cos(theta), np.sin(theta), 0],
                                [-np.sin(theta),
                                 np.cos(theta), 0], [0, 0, 1]])
            block[:, :3] = np.dot(block_[:, :3], rot_mat) * scale_factor
        else:
            theta = self.angle
            transform_mat = np.array([[np.cos(theta),
                                       np.sin(theta), 0],
                                      [-np.sin(theta),
                                       np.cos(theta), 0], [0, 0, 1]])
            block[...] = block_[...]
            block[:, :3] = np.dot(block[:, :3], transform_mat)

        block[:, 3] = block_[:, 3]
        pc_ = np.round(block[:, :3] / self.voxel_size).astype(np.int32)
        pc_ -= pc_.min(0, keepdims=1)

        feat_ = block

        _, inds, inverse_map = sparse_quantize(pc_,
                                               return_index=True,
                                               return_inverse=True)

        if 'train' in self.split:
            if len(inds) > self.num_points:
                inds = np.random.choice(inds, self.num_points, replace=False)

        pc = pc_[inds]
        feat = feat_[inds]
        labels = labels_[inds]
        lidar = SparseTensor(feat, pc)
        labels = SparseTensor(labels, pc)
        labels_ = SparseTensor(labels_, pc_)
        inverse_map = SparseTensor(inverse_map, pc_)

        return {
            'lidar': lidar,
            'targets': labels,
            'targets_mapped': labels_,
            'inverse_map': inverse_map,
            'file_name': self.files[index]
        }

    @staticmethod
    def collate_fn(inputs):
        return sparse_collate_fn(inputs)
