import math
import numpy as np

MESSAGES_HIGH = ["Error", "Slow move", "Running"]
MESSAGES_LOW = ["Lying on the table", "Lying on the chair", "Reading", "Sitting"]

clusters_levels = []
with open('cluster_centers_levels') as f:
    for line in f:
        splits = line.strip().split()
        cluster = []
        for s in splits:
            cluster.append(float(s))
        clusters_levels.append(cluster)

clusters_level0 = []
with open('cluster_centers_level0') as f:
    for line in f:
        splits = line.strip().split()
        cluster = []
        for s in splits:
            cluster.append(float(s))
        clusters_level0.append(cluster)



def extract_feature(data_list):
    x = [data[1] for data in data_list]
    y = [data[2] for data in data_list]
    z = [data[3] for data in data_list]
    mag_list = [math.sqrt(data[1] * data[1] + data[2] * data[2] + data[3] * data[3]) for data in data_list]
    ave = [np.mean(x), np.mean(y), np.mean(z)]
    std = [np.std(x), np.std(y), np.std(z)]
    mag_ave = np.mean(mag_list)
    mag_std = np.std(mag_list)

    angle1 = ave[1] / math.sqrt(ave[1] * ave[1] + ave[2] * ave[2])
    angle2 = ave[2] / math.sqrt(ave[1] * ave[1] + ave[2] * ave[2])

    return (std + [mag_std], [angle1, angle2])

def get_cluster(feature, clusters):
    cluster_num = -1
    min_dist = 10000
    for cluster_idx, cluster in enumerate(clusters):
        dist = 0
        for idx, val in enumerate(cluster):
            dist += (val - feature[idx]) * (val - feature[idx])
        if dist < min_dist:
            min_dist = dist
            cluster_num = cluster_idx

    return cluster_num

def classify(feature_levels, feature_level0):
    activity_level = get_cluster(feature_levels, clusters_levels)
    if activity_level > 0:
        return MESSAGES_HIGH[activity_level]
    else:
        activity_idx = get_cluster(feature_level0, clusters_level0)
        return MESSAGES_LOW[activity_idx]
