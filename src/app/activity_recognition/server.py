import os
import sys
import time
import subprocess
import tempfile
import shutil
import socket
import struct
from multiprocessing import Process, Manager
sys.path.insert(0, "lib")
import svmutil
#import sys
#sys.path[:0] = ["/usr/lib/pymodules/python2.7"] # this is to ensure we use the right opencv...
import Image, cv, cv2
from cStringIO import StringIO

TMP_DIR = "tmp"
FEATURE_DIR = os.path.join(TMP_DIR, 'feature')
bin_path = "bin"
centers_path = "cluster_centers"
model_path = "models"

frames = []
ps = []
queue = Manager().Queue()
chop_length = 90
chop_period = 60
chop_counter = 60

w = 160
h = 120
selected_feature = "mosift"
descriptor = "all"
n_clusters = 1024

model_names = ["SayHi", "Clapping", "TurnAround", "Squat", "ExtendingHands"]
svmmodels = []
for model_name in model_names:
    svmmodels.append(svmutil.svm_load_model("%s/model_%s_%s_%s_%d" % (model_path, model_name, selected_feature, descriptor, n_clusters)))
MESSAGES = ["Someone is waving to you.", "Someone is clapping.", "Someone is turning around.", "Someone just squated.", "Someone wants to shake hands with you."]

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

def detectActivity(frames, queue):
    current_time = time.time()

    # Write into a video chunk
    fd, video_path = tempfile.mkstemp(dir='tmp/all', prefix='video', suffix='.avi')
    print "Created temporary video file: %s" % video_path
    videoWriter = cv.CreateVideoWriter(video_path, cv.CV_FOURCC('X', 'V', 'I', 'D'), 30, (160, 120), True)
    if not videoWriter:
        print "Error in creating video writer"
        sys.exit(1)
    else:
        for frame in frames:
            cv.WriteFrame(videoWriter, frame)

    video_spent_time = time.time() - current_time
    print "\n\n\n\n\n\n\nVideo write spent time: %f\n\n\n\n\n\n\n" % video_spent_time

    # Extract features from video file (in .spbof file)
    video_name = os.path.basename(video_path).split('.avi')[0]

    spbof_file = "%s/%s_%s_%d_%s.spbof" % (FEATURE_DIR, selected_feature, descriptor, n_clusters, video_name)
    raw_file = "%s/%s_raw_%s.txt" % (FEATURE_DIR, selected_feature, video_name)
    txyc_file = "%s/%s_%s_%d_%s.txyc" % (FEATURE_DIR, selected_feature, descriptor, n_clusters, video_name)
    tmp_video_file = "%s/%s_%s.avi" % (FEATURE_DIR, selected_feature, video_name)
    stable_video_file = "%s/stable_%s_%s.avi" % (FEATURE_DIR, selected_feature, video_name)
    input_file = "%s/input_%s_%s.txt" % (FEATURE_DIR, selected_feature, video_name)
    center_file = "%s/centers_%s_%s_%d" % (centers_path, selected_feature, descriptor, n_clusters)

    subprocess.call(['avconv', '-i', video_path, '-c:v', 'libxvid', '-s', '%dx%d' % (w,h),
                     '-r', '30', tmp_video_file])
    #tmp_video_file = video_path
    with open(raw_file, 'wb') as out:
        if selected_feature == "traj":
            subprocess.call(['%s/DenseTrack' % bin_path, tmp_video_file],
                            stdout = out)
        elif selected_feature == "trajS":
            subprocess.call(['%s/stabilize_c' % bin_path, tmp_video_file, stable_video_file])
            subprocess.call(['%s/DenseTrack' % bin_path, stable_video_file],
                            stdout = out)
            os.remove(stable_video_file)
        elif selected_feature == "mosift":
            #os.system("bin/siftmotionffmpeg -r -t 1 -k 2 tmp/all/%s.avi tmp/all/mosift_raw_%s.txt" % (video_name,video_name))
            subprocess.call(['%s/siftmotionffmpeg' % bin_path, '-r', '-t', '1', '-k', '2',
                             tmp_video_file, raw_file])
        elif selected_feature == "stip":
            with open(input_file, 'w') as f_input:
                f_input.write(os.path.basename(tmp_video_file).split('.')[0])
            subprocess.call(['%s/stipdet' % bin_path, '-i', input_file, '-vpath', './%s/all/' % (TMP_DIR),
                             '-ext', '.avi', '-o', raw_file, '-det', 'harris3d', '-vis', 'no'])
            os.remove(input_file)
        elif selected_feature == "stipS":
            subprocess.call(['%s/stabilize_c' % bin_path, tmp_video_file, stable_video_file])
            with open(input_file, 'w') as f_input:
                f_input.write(os.path.basename(stable_video_file).split('.')[0])
            subprocess.call(['%s/stipdet' % bin_path, '-i', input_file, '-vpath', './%s/all/' % (TMP_DIR),
                             '-ext', '.avi', '-o', raw_file, '-det', 'harris3d', '-vis', 'no'])
            os.remove(stable_video_file)
            os.remove(input_file)

    subprocess.call(['%s/txyc' % bin_path, center_file, str(n_clusters), raw_file, txyc_file, selected_feature, descriptor])
    subprocess.call(['%s/spbof' % bin_path, txyc_file, str(w), str(h), str(n_clusters), '10', spbof_file, '1'])

    os.remove(raw_file)
    os.remove(txyc_file)

    # Detect activity from MoSIFT feature vectors
    feature_vec = load_data(spbof_file)
    #os.remove(spbof_file)

    model_idx = -1
    max_score = 0
    for idx, model_name in enumerate(model_names):
        #svmmodel = svmutil.svm_load_model("model_%s" % model_name)
        p_labs, p_acc, p_vals = svmutil.svm_predict([1], [feature_vec], svmmodels[idx], "-b 1")
        labels = svmmodels[idx].get_labels()
        val = p_vals[0][0] * labels[0] + p_vals[0][1] * labels[1]
        if val > max_score:
            max_score = val 
            model_idx = idx

    model_name = model_names[model_idx]
    if max_score > 0.4: # Activity is detected
        shutil.copyfile("tmp/all/%s.avi" % video_name, "tmp/activity/%s_%s_%d.avi" % (video_name, model_name, int(max_score * 100)))
        os.remove(video_path)
        
        return

        if not queue.empty():
            previous_time = queue.get()
            queue.put(previous_time)
            if current_time - previous_time <= 6:
                return
            previous_time = queue.get()

        queue.put(current_time)

        for i in xrange(10):
            print ""
        print "ACTIVITY DETECTED: %s!" % model_name
        print "Current time: %f" % current_time
        print "Confidence score: %f" % max_score
        for i in xrange(10):
            print ""

        UDP_IP = "128.237.197.216"
        UDP_PORT = 18080
        MESSAGE = MESSAGES[model_idx]

        udpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udpSocket.sendto(MESSAGE, (UDP_IP, UDP_PORT))
    else:
        print "Max confidence score: %f, activity is: %s" % (max_score, model_name)
        os.rename("tmp/all/%s.avi" % video_name, "tmp/all/%s_%s_%d.avi" % (video_name, model_name, int(max_score * 100)))

def processFrame(data):
    t_start = time.time()

    file_jpgdata = StringIO(data)
    image = Image.open(file_jpgdata)
    frame_bgr = cv.CreateImageHeader(image.size, cv.IPL_DEPTH_8U, 3)
    cv.SetData(frame_bgr, image.tostring())
    # convert frame from BGR to RGB
    # in Image, it's RGB; in cv, it's BGR
    frame = cv.CreateImage(cv.GetSize(frame_bgr),cv.IPL_DEPTH_8U, 3)
    cv.CvtColor(frame_bgr, frame, cv.CV_BGR2RGB)

    # Show real-time captured image
    #cv.NamedWindow('captured video', cv.CV_WINDOW_AUTOSIZE)
    #cv.ShowImage('captured video', frame) 
    #cv.WaitKey(1)

    frames.append(frame)
    if len(frames) > chop_length:
        del frames[0]
    global chop_counter
    if len(frames) == chop_length:
        if chop_counter == chop_period:
            chop_counter = 1
            #print "Start detecting activity... "
            p = Process(target = detectActivity, args = (frames, queue, ))
            p.start()
            ps.append(p)
            for p in ps:
                if not p.is_alive():
                    ps.remove(p)
            print "number of processes now: %d" % len(ps)
        else:
            chop_counter += 1

    t_end = time.time()
    #print "Time spent to process one frame: %f" % (t_end - t_start)

def startTcpServer(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(0)

    while True:
        print "waiting for connection"
        conn, addr = s.accept()
        print 'Connected by', addr

        received = conn.recv(4)
        while received:
            frame_size = struct.unpack("!I", received)[0]
            #print "Frame size = %d" % frame_size
            data = ""
            received_size = 0
            while received_size < frame_size:
                data_tmp = conn.recv(frame_size - received_size)
                data += data_tmp
                received_size += len(data_tmp)
            #print "received %d bytes" % len(data)

            processFrame(data)

            received = conn.recv(4)

    s.close()

if __name__ == "__main__":
    if not os.path.isdir(TMP_DIR):
        os.mkdir(TMP_DIR)
        os.mkdir('%s/all' % TMP_DIR)
        os.mkdir('%s/activity' % TMP_DIR)
        os.mkdir(FEATURE_DIR)
    startTcpServer("", 8080)
