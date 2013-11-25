CC	= g++
#CFLAGS	= -O3 -static -D__STDC_CONSTANT_MACROS
CFLAGS	= -O3  -D__STDC_CONSTANT_MACROS
BIN_DIR	= ./bin
INC_DIR	= ./include
LIB_DIR	= ./lib
OBJ	= SiftMotionFFmpeg.o Motion_SIFT_feature.o convert.o AFGVideo.FF.o
INCL	= -I$(INC_DIR) 			\
          `pkg-config --cflags opencv` \
          `pkg-config --cflags libavformat libavcodec libavformat libswscale` 
LIBS	= -L$(LIB_DIR) \
	  -L/usr/lib/x86_64-linux-gnu \
	  -lopencv_highgui -lopencv_imgproc -lopencv_video -lopencv_core -lswscale -lavformat -lavcodec -lavutil -lavfilter -lavdevice
BIN     = siftmotionffmpeg

all: $(BIN)
	chmod +x $(BIN_DIR)/siftmotionffmpeg

siftmotionffmpeg: $(OBJ)
	$(CC) $(CFLAGS) $(LIB_DIR)/SiftMotionFFmpeg.o $(LIB_DIR)/Motion_SIFT_feature.o $(LIB_DIR)/convert.o $(LIB_DIR)/AFGVideo.FF.o $(LIBS) -o $(BIN_DIR)/$@

SiftMotionFFmpeg.o: SiftMotionFFmpeg.cpp
	$(CC) $(CFLAGS) $(INCL) -c SiftMotionFFmpeg.cpp -o $(LIB_DIR)/$@

Motion_SIFT_feature.o: Motion_SIFT_feature.cpp
	$(CC) $(CFLAGS) $(INCL) -c Motion_SIFT_feature.cpp -o $(LIB_DIR)/$@

AFGVideo.FF.o: AFGVideo.FF.cpp
	$(CC) $(CFLAGS) $(INCL) -c AFGVideo.FF.cpp -o $(LIB_DIR)/$@

convert.o: convert.cpp
	$(CC) $(CFLAGS) $(INCL) -c convert.cpp -o $(LIB_DIR)/$@

clean:
	rm -f *~ ./include/*~ ./lib/*.o core

.PHONY: clean
