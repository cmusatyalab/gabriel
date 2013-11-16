#! /usr/bin/env python

import os
import math
import numpy as np

WID_SIZE = 50
OVERLAP = 25

def extract_feature(mag_list, data_list):
    x = [data[0] for data in data_list]
    y = [data[1] for data in data_list]
    z = [data[2] for data in data_list]
    ave = [np.mean(x), np.mean(y), np.mean(z)]
    std = [np.std(x), np.std(y), np.std(z)]
    mag_ave = np.mean(mag_list)
    mag_std = np.std(mag_list)
    return std + [mag_std]

def get_features_from_file(file_name):
    acc_mag_list = []
    acc_data_list = []
    feature_list = []
    with open(file_name) as f:
        for line in f:
            splits = line.strip().split()
            acc_data = [float(splits[1]), float(splits[2]), float(splits[3])]
            acc_data_list.append(acc_data)
            acc_mag = math.sqrt(acc_data[0] * acc_data[0] + acc_data[1] * acc_data[1] + acc_data[2] * acc_data[2])
            acc_mag_list.append(acc_mag)
            if len(acc_mag_list) == WID_SIZE:
                feature = extract_feature(acc_mag_list, acc_data_list)
                feature_list.append(feature)
                for i in xrange(WID_SIZE - OVERLAP):
                    del(acc_mag_list[0])
                    del(acc_data_list[0])
    return feature_list

def cluster(features):
    from sklearn.cluster import KMeans, MiniBatchKMeans

    k_means = KMeans(init = 'k-means++', n_clusters = 3, n_init = 12, n_jobs = 6)
    k_means.fit(features)
    print k_means.labels_

    return k_means.cluster_centers_

def main():
    # preprocessing
    feature_list = []
    for exp_i in xrange(1, 5):
        file_name = os.path.join("training_data", "experiment%d" % exp_i, "acc.txt")
        feature_list_curr = get_features_from_file(file_name)
        feature_list += feature_list_curr

    cluster_centers = cluster(feature_list)

    with open("cluster_centers", 'w') as f:
        for cluster_center in cluster_centers:
            output_string = ' '.join([str(x) for x in cluster_center])
            f.write("%s\n" % output_string)

if __name__ == "__main__":
    main()
