CC	= g++
BIN_DIR	= ./bin
INC_DIR	= ./include
LIB_DIR	= ./lib
OBJ	= SiftMotionFFmpeg.o Motion_SIFT_feature.o convert.o AFGVideo.FF.o
INCL	= -I$(INC_DIR) 			\
          `pkg-config --cflags opencv` \
          `pkg-config --cflags libavformat libavcodec libavformat libswscale`
LIBS	= -L$(LIB_DIR) \
	  -L/usr/lib/x86_64-linux-gnu 
BIN     = txyc bof spbof

all: $(BIN)
	chmod +x $(BIN_DIR)/txyc
	chmod +x $(BIN_DIR)/bof
	chmod +x $(BIN_DIR)/spbof

txyc: txyc.o convert.o 
	$(CC) $(CFLAGS) $(LIB_DIR)/convert.o $(LIB_DIR)/txyc.o -o $(BIN_DIR)/$@

txyc.o: txyc.cpp
	$(CC) $(CFLAGS) $(INCL) -c txyc.cpp -o $(LIB_DIR)/$@

bof: bof.o convert.o 
	$(CC) $(CFLAGS) $(LIB_DIR)/convert.o $(LIB_DIR)/bof.o -o $(BIN_DIR)/$@

bof.o: bof.cpp
	$(CC) $(CFLAGS) $(INCL) -c bof.cpp -o $(LIB_DIR)/$@

spbof: spbof.cpp 
	$(CC) -O3 spbof.cpp -o $(BIN_DIR)/$@

convert.o: convert.cpp
	$(CC) $(CFLAGS) $(INCL) -c convert.cpp -o $(LIB_DIR)/$@

init:
	mkdir -p bin lib
clean:
	rm -f *~ ./include/*~ ./lib/*.o ./bin/*

.PHONY: clean init
