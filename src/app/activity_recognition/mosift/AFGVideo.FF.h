extern "C" {

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libswscale/swscale.h>

};


class AFGvideo {
public:
    int              debug;
    int              hr;
    char            *hrs;
    int              width;
    int              height;
    int              numBytes;
    int64_t          frames;
    int64_t          length;
    int64_t          cur_loc;
    double           cur_pts;
    double           nat_pts;
    double           fps;
    double           start_sec;
    double           vid_start_sec;
    double           len_sec;

#ifdef _WIN32 
__declspec(dllexport)
#endif
    AFGvideo(char *file);
#ifdef _WIN32 
__declspec(dllexport)
#endif
    AFGvideo(char *file, PixelFormat pf);
#ifdef _WIN32 
__declspec(dllexport)
#endif
    AFGvideo(char *file, PixelFormat pf, int dbg);
    void AFGinit(char *file, PixelFormat pf, int dbg);
#ifdef _WIN32 
__declspec(dllexport)
#endif
    ~AFGvideo();
#ifdef _WIN32 
__declspec(dllexport)
#endif
    int Next();
#ifdef _WIN32 
__declspec(dllexport)
#endif
    int Next(int cnt);
#ifdef _WIN32 
__declspec(dllexport)
#endif
    unsigned char *buf();
#ifdef _WIN32 
__declspec(dllexport)
#endif
    int NEXT(int cnt);
#ifdef _WIN32 
__declspec(dllexport)
#endif
    int Seek(double pts);
#ifdef _WIN32 
__declspec(dllexport)
#endif
    int Skip(int cnt);
#ifdef _WIN32 
__declspec(dllexport)
#endif
    void Free();

protected:
//  WCHAR            wFile[_MAX_PATH];

private:
    PixelFormat      pix_fmt;
    AVFormatContext *pFormatCtx;
    int              videoStream;
    double           base;
    AVCodecContext  *pCodecCtx;
    AVCodec         *pCodec;
    AVPacket         packet;
    uint8_t         *buffer;
    AVFrame         *pFrame; 
    AVFrame         *pFrameRGB;
    double           first_pts;
    AVFrame        *Next_PTS();
    int64_t         cur_frame;
    int64_t         cur_time;
};
