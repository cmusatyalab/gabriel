#! /usr/bin/env python

import os

from analysis import extract_feature

WID_SIZE = 50
OVERLAP = 25

def get_features_from_file(file_name):
    acc_data_list = []
    feature_list = []
    with open(file_name) as f:
        for line in f:
            splits = line.strip().split()
            acc_data = [int(splits[0]), float(splits[1]), float(splits[2]), float(splits[3])]
            acc_data_list.append(acc_data)
            if len(acc_data_list) == WID_SIZE:
                feature_levels, feature_level0 = extract_feature(acc_data_list)
                feature_list.append([feature_levels, feature_level0])
                for i in xrange(WID_SIZE - OVERLAP):
                    del(acc_data_list[0])
    return feature_list

def cluster(features, n_clusters):
    from sklearn.cluster import KMeans, MiniBatchKMeans

    k_means = KMeans(init = 'k-means++', n_clusters = n_clusters, n_init = 12, n_jobs = 6)
    k_means.fit(features)
    print k_means.labels_

    return k_means.cluster_centers_

def cluster_activity_levels():
    # preprocessing
    feature_list = []
    for exp_i in xrange(1, 5):
        file_name = os.path.join("training_data", "experiment%d" % exp_i, "acc.txt")
        feature_list_curr = get_features_from_file(file_name)
        feature_list += feature_list_curr

    feature_list = [x[0] for x in feature_list]
    cluster_centers = cluster(feature_list, 3)

    with open("cluster_centers_levels", 'w') as f:
        for cluster_center in cluster_centers:
            output_string = ' '.join([str(x) for x in cluster_center])
            f.write("%s\n" % output_string)

def cluster_lie_sit():
    # preprocessing
    feature_list = []
    for exp_i in xrange(5, 8):
        file_name = os.path.join("training_data", "experiment%d" % exp_i, "acc.txt")
        feature_list_curr = get_features_from_file(file_name)
        feature_list += feature_list_curr

    feature_list = [x[1] for x in feature_list]
    cluster_centers = cluster(feature_list, 4)

    with open("cluster_centers_level0", 'w') as f:
        for cluster_center in cluster_centers:
            output_string = ' '.join([str(x) for x in cluster_center])
            f.write("%s\n" % output_string)

def main():
    cluster_activity_levels()
    cluster_lie_sit()
    
if __name__ == "__main__":
    main()
