LIBMOPEDDIR := ./moped

OBJS =	    

TARGET = ./moped_server


# Compiler flags 
CXX = g++-4.4

CXXFLAGS = -O3 -Wall -march=native -mfpmath=sse -fopenmp 
CXXFLAGS += -ffast-math -fno-trapping-math -fsingle-precision-constant -funroll-loops

INCLUDES = -I$(LIBMOPEDDIR)/include/ 
LIBS = -lpthread -lgomp -lGLEW -lglut -lIL `pkg-config --libs opencv glu glew`
#LIBS = -lpthread -lgomp -lGLEW -lglut -lIL -lcv -lhighgui -lcvaux -lpthread

# Targets
.PHONY : all clean

all : $(TARGET) 

$(TARGET):	$(OBJS)
#	$(CXX) $(CXXFLAGS) -o $(TARGET) $(INCLUDES) -Wl, -Xlinker --start-group $(OBJS) -Xlinker --end-group $(LIBMOPEDDIR)/lib/libmoped.a $(LIBS) 
	$(CXX) $(CXXFLAGS) $(INCLUDES) -Wl,--start-group $(DLIBS)  $(OBJS)  $(LIBMOPEDDIR)/lib/libmoped.a moped_server.cpp -o $(TARGET) $(LIBS)

clean:
	$(RM) $(TARGET)
	$(RM) -rf *.o

