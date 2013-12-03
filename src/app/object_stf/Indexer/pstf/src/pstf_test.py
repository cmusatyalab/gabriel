""" Demo that shows how STF's forest0 classifies an image """
# <demo> silent
import logging
import sys
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

sys.path.append('python_stf.egg')
sys.path.append('pymorph.egg')

label_class = 1
close_size = 5
open_size = 5

STRIDE0 = 1
STRIDE1 = 1
ALPHA = 0.5

#if __name__ == '__main__':
#  from IPython.demo import Demo
#  d = Demo(sys.argv[0], ' '.join(sys.argv[1:]))
#  while not d.finished: d()
#  raise SystemExit

import Image
import math
import numpy as np
import pymorph

import pstf
#from matplotlib.pyplot import figure, show

class LeafImageViewer(object):
    def __init__(self, plot, leafimage, hist, labels=None):
        plot.set_title('use scroll wheel to navigate')
        self.plot = plot
        self.image = leafimage.image
        self.hist = hist
        self.labels = labels

        self.MAX_DEPTH = int(math.log(hist.shape[0], 2))
        self.NTREES = hist.shape[1]
        self.NLABELS = hist.shape[2]

        if self.labels:
            self.index = 0
            self.maxindex = self.NLABELS-1
        else:
            self.index = self.MAX_DEPTH
            self.maxindex = self.MAX_DEPTH

        imgs = ( self.hist[self.image[i], i] for i in xrange(self.NTREES) )
        img = np.true_divide(np.sum(imgs, axis=0).argmax(axis=2), self.NLABELS)

        self.im = plot.imshow(img)
        self.update()

    def onscroll(self, event):
        if event.button == 'up':
            self.index = self.index + 1
        else:
            self.index = self.index - 1
        self.index = np.clip(self.index, 0, self.maxindex)
        self.update()

    def update(self):
        data = self.image
        if not self.labels:
            data = data / (2**(self.maxindex-self.index))

        imgs = ( self.hist[data[i], i] for i in xrange(self.NTREES) )
        img = np.sum(imgs, axis=0)

        if not self.labels:
            img = np.true_divide(img.argmax(axis=2), self.NLABELS)
            self.im.set_cmap('jet')
            self.plot.set_ylabel('depth %s' % self.index)
        else:
            img = np.true_divide(img, img.sum(axis=2)[...,np.newaxis])
            img = img[...,self.index]
            self.im.set_cmap('hot')
            self.plot.set_ylabel(self.labels[self.index][0])

        self.im.set_data(img)
        self.im.axes.figure.canvas.draw()

def explore_leafimage(img, leafimage, hist, labels=None):
    fig = figure()
    plot = fig.add_subplot(121)
    plot.imshow(np.asarray(img))

    plot = fig.add_subplot(122)
    tracker = LeafImageViewer(plot, leafimage, hist, labels)
    fig.canvas.mpl_connect('scroll_event', tracker.onscroll)
    show()

if sys.argv[2:]:
    palette, colors = pstf.image.load_palette('labels.txt')
    reference = pstf.image.image_to_label(Image.open(sys.argv[2]), palette)
else:
    reference = None

def compute_confusion_matrix(image, reference, colors):
    from cStringIO import StringIO
    sys.stdout = StringIO()

    print " "*10,
    for color in colors[1:]:
        print ",%10s" % color[0],
    print

    for idx, color in enumerate(colors):
        masked_results = image * (reference==idx)
        counts = np.bincount(masked_results.ravel(), minlength=len(colors))[1:]
        print "%10s" % color[0],
        for value in counts:
           print ",%10d" % value,
        print ",%10d" % counts.sum()

    counts = np.bincount(image.ravel(), minlength=len(colors))[1:]
    print " "*10,
    for value in counts:
        print ",%10d" % value,
    print

    confusion_matrix = sys.stdout.getvalue()
    sys.stdout = sys.__stdout__
    return confusion_matrix

def cm_head(colors):
    print "trees",
    for fr in colors:
        for to in colors[1:]:
            print ", %s > %s" % (fr[0][0],to[0][0]),
    print

def cm_body(image, reference, colors):
    for idx, color in enumerate(colors):
        masked_results = image * (reference==idx)
        counts = np.bincount(masked_results.ravel(), minlength=len(colors))[1:]
        for value in counts:
           print ", %d" % value,
    print

# <demo> --- stop ---
forest0 = pstf.Forest.load(open('forest0.npz'))
hist0 = pstf.forest.load_histogram('hist0.npy')

forest1 = pstf.Forest.load(open('forest1.npz'))
hist1 = pstf.forest.load_histogram('hist1.npy')

NLABELS = hist0.shape[2]
training_bosts = pstf.forest.load_bosts('bosts.npy')
svmmodels = [ pstf.svm_.load_model('svmmodel%d' % i)
              for i in range(1,NLABELS) ]

close_SE = pymorph.sedisk(close_size)
open_SE  = pymorph.sedisk(open_size)

# <demo> --- stop ---
testimage = sys.argv[1]
image = Image.open(testimage)
cielab_image = pstf.image.convert_to_cielab(np.asarray(image))

# <demo> --- stop ---
leafimage0,_ = forest0.compute_leafimage((cielab_image, None), STRIDE0)
#explore_leafimage(image, leafimage0, hist0)
#explore_leafimage(image, leafimage0, hist0, labels)

# <demo> --- stop ---
bost = leafimage0.compute_bost()
bost = pstf.forest.normalize(bost).T
vector = pstf.svm_.make_vectors(bost, training_bosts)

svmresults = [0] + [ pstf.svm_.svm_predict(m, vector[0])
                     for m in svmmodels ]
score = svmresults[label_class]
ILP = np.power(svmresults, ALPHA)
# <demo> --- stop ---

distimage0 = leafimage0.compute_distimage(hist0)

distimage0 = np.true_divide(distimage0, distimage0.sum(axis=2)[...,np.newaxis])
leafimage1,_ = forest1.compute_leafimage(distimage0.cumsum(axis=0).cumsum(axis=1), STRIDE1, IS_INTEGRAL=True)

#explore_leafimage(image, leafimage1, hist1)
#explore_leafimage(image, leafimage1, hist1, labels)

distimage1 = leafimage1.compute_distimage(hist1)

resultimage = distimage0 * distimage1 * ILP

result = np.true_divide(resultimage, resultimage.sum(axis=2)[...,np.newaxis])

Image.fromarray(np.uint8(result[...,1:]*255)).save('colors.png')

result_max = result.argmax(axis=2)
Image.fromarray(np.uint8((result_max/result_max.max())*255)).save('argmax.png')

if reference is not None:
    confusion_matrix = compute_confusion_matrix(result_max, reference, colors)
    print "STF confusion matrix"
    print confusion_matrix
    open('confusion_matrix.txt','w').write(confusion_matrix)

    cm_head(colors)
    print "forest0 segmentation"
    imgs = [ hist0[leafimage0.image[i], i]
             for i in xrange(leafimage0.TREE_COUNT) ]
    for i in xrange(leafimage0.TREE_COUNT):
        distimage = np.sum(imgs[:i+1], axis=0)
        segmentation = distimage.argmax(axis=2)
        print i+1,
        cm_body(segmentation, reference, colors)
    print

    print "STF segmentation (x forest0 trees N forest1 trees)"
    for i in xrange(leafimage0.TREE_COUNT):
        distimage = np.sum(imgs[:i+1], axis=0)
        # normalize
        distimage = np.true_divide(distimage, distimage.sum(axis=2)[...,np.newaxis])
        leafimage,_ = forest1.compute_leafimage(distimage.cumsum(axis=0).cumsum(axis=1), STRIDE1, IS_INTEGRAL=True)
        distimage = leafimage.compute_distimage(hist1)

        segmentation = distimage.argmax(axis=2)
        print i+1,
        cm_body(segmentation, reference, colors)
    print

    print "STF segmentation (N forest0 trees x forest1 trees)"
    imgs = [ hist1[leafimage1.image[i], i]
             for i in xrange(leafimage1.TREE_COUNT) ]
    for i in xrange(leafimage1.TREE_COUNT):
        distimage = np.sum(imgs[:i+1], axis=0)
        segmentation = distimage.argmax(axis=2)
        print i+1,
        cm_body(segmentation, reference, colors)
    print


#fig = figure()
#plot = fig.add_subplot(111)
#plot.imshow(distimage1.argmax(axis=2))
#show()

##
## postprocessing result to identify clusters
##
#THRESHOLD = .5
#CLOSER = pymorph.sedisk(1)
#OPENER = pymorph.sedisk(8)
#
#mcytes = result[...,2] > THRESHOLD
#mcytes = pymorph.close(mcytes, CLOSER)
#mcytes = pymorph.open(mcytes, OPENER)
#
#markup = np.uint8(np.expand_dims(mcytes, axis=2) * [[(0,255,0,255)]])
#Image.fromarray(markup).save('result.png')

#imshow(result[...,1:])
