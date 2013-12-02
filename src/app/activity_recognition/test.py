import sys
sys.path.insert(0, "../lib")
import svmutil
import cv, cv2
import subprocess
import shutil

selected_feature = "mosift"
descriptor = "MOTION"
n_clusters = 1024
model_path = "../models"
bin_path = "../bin"
centers_path = "../cluster_centers"
model_names = ["SayHi", "Clapping", "TurnAround", "Squat", "ExtendingHands"]
svmmodels = []
for model_name in model_names:
    svmmodels.append(svmutil.svm_load_model("%s/model_%s_%s_%s_%d" % (model_path, model_name, selected_feature, descriptor, n_clusters)))
MESSAGES = ["Someone is waving to you.", "Someone is clapping.", "Someone is turning around.", "Someone just squated.", "Someone wants to shake hands with you."]

spbof_file = "test_spbof.txt"
txyc_file = "test_txyc.txt"
raw_file = "test_raw.txt"
center_file = "%s/centers_%s_%s_%d" % (centers_path, selected_feature, descriptor, n_clusters)
video_path = "test.avi"

w = 160
h = 120

def load_data(spbof_file):
    with open(spbof_file, 'r') as f:
        v_data = f.readline().strip().split()
        v = {}
        for v_dim in v_data:
            v_tmp = v_dim.split(':')
            idx = int(v_tmp[0])
            val = float(v_tmp[1])
            v[idx] = val

    return v

def func1():
    videoWriter = cv2.VideoWriter(video_path, cv.CV_FOURCC('X', 'V', 'I', 'D'), 10, (160, 120), True)
    for i in xrange(30):
        cv_image = cv2.imread("seq%d.jpg" % i, -1)
        videoWriter.write(cv_image)
    del videoWriter

    subprocess.call(['%s/siftmotionffmpeg_old_unS' % bin_path, '-r', '-t', '1', '-k', '0',
                             video_path, raw_file])
    subprocess.call(['%s/txyc_old' % bin_path, center_file, str(n_clusters), raw_file, txyc_file, selected_feature, descriptor])
    subprocess.call(['%s/spbof' % bin_path, txyc_file, str(w), str(h), str(n_clusters), '10', spbof_file, '1'])

def func2():
    features = []
    for i in xrange(29):
        shutil.copyfile("seq%d.jpg" % i, "tmp/tmp0.jpg")
        shutil.copyfile("seq%d.jpg" % (i + 1), "tmp/tmp1.jpg")
        subprocess.call(['%s/siftmotionffmpeg_unS' % bin_path, '-r', video_path, raw_file])
        with open(raw_file) as f:
            feature = f.readlines()
            features += feature
    with open(raw_file, 'w') as f:
        for feature in features:
            f.write(feature)
    subprocess.call(['%s/txyc_old' % bin_path, center_file, str(n_clusters), raw_file, txyc_file, selected_feature, descriptor])
    subprocess.call(['%s/spbof' % bin_path, txyc_file, str(w), str(h), str(n_clusters), '10', spbof_file, '1'])

def main():
    #func1()
    func2()

    feature_vec = load_data(spbof_file)
    #print feature_vec

    model_idx = -1
    max_score = 0
    for idx, model_name in enumerate(model_names):
        if idx == len(model_names) - 1:
            break
        #svmmodel = svmutil.svm_load_model("model_%s" % model_name)
        p_labs, p_acc, p_vals = svmutil.svm_predict([1], [feature_vec], svmmodels[idx], "-b 1 -q")
        labels = svmmodels[idx].get_labels()
        val = p_vals[0][0] * labels[0] + p_vals[0][1] * labels[1]
        print (val, model_name)
        if val > max_score:
            max_score = val
            model_idx = idx

    print (max_score, model_idx)

main()
