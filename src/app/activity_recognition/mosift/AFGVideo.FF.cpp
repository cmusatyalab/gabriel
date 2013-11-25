// AFGVIDEO.FF.cpp : Defines the entry point for the DLL application.
//

//#include "stdafx.h"
#include <stdio.h>
#include <stdlib.h>
#include "AFGVideo.FF.h"
#include "math.h"


/* 
	Acknowledgement:
   This work is based on the tutorial:
   	Using libavformat and libavcodec
	Martin Boehme (boehme@inb.uni-luebeckREMOVETHIS.de)

	http://www.inb.uni-luebeck.de/~boehme/using_libavcodec.html
	http://www.inb.uni-luebeck.de/~boehme/libavcodec_update.html
	http://www.inb.uni-luebeck.de/~boehme/avcodec_sample.cpp
*/

/* NOTES:
 0. Open Issues:
   a. Seek does not currently work in the underlying libavcodec/libavformat
   b. Frame #1 fetched here corresponds to frame #3 (and so on) according to MS filter
      graph ... who is right.  (The correspondence is "correct" for virtual dub.)
      THIS MAY ONLY APPLY TO CERTAIN DAMAGED MPEGS
   c. cur_pts reflects roughly the pts.  nat_pts is cur_pts minus a fudge.  The fudge 
      is what ffmpeg uses as the start_sec. But we have a video stream start, an audio
      stream start and this ffmpeg start, which is the correct time to use?

 1. http://lists.mplayerhq.hu/pipermail/ffmpeg-user/2005-September/001245.html
    informs us that avcodec_decode_frame() buffers and handles the reordering between
    dts and pts
    (previous version of this AFGVIDEO.FF.cpp had scaffolding[unnecessary] to do this)
 */
#define ROUND (.001)
/*
BOOL APIENTRY DllMain( HANDLE hModule, 
                       DWORD  ul_reason_for_call, 
                       LPVOID lpReserved
					 )
{
    return TRUE;
}
*/

// avcodec_sample.0.4.9.cpp

// A small sample program that shows how to use libavformat and libavcodec to
// read video from a file.
//
// This version is for the 0.4.9-pre1 release of ffmpeg. This release adds the
// av_read_frame() API call, which simplifies the reading of video frames 
// considerably. 
//
// Use
//
// g++ -o avcodec_sample.0.4.9 avcodec_sample.0.4.9.cpp -lavformat -lavcodec \
//     -lz
//
// to build (assuming libavformat and libavcodec are correctly installed on
// your system).
//
// Run using
//
// avcodec_sample.0.4.9 myvideofile.mpg
//
// to write the first five frames from "myvideofile.mpg" to disk in PPM
// format.


#ifdef _WIN32
__declspec(dllexport)
#endif

AFGvideo::AFGvideo(char *file)
{
    AFGinit(file, PIX_FMT_RGB24, 0);
}

#ifdef _WIN32
__declspec(dllexport)
#endif
AFGvideo::AFGvideo(char *file, PixelFormat pf)
{
    AFGinit(file, pf, 0);

}

#ifdef _WIN32
__declspec(dllexport)
#endif
    AFGvideo::AFGvideo(char *file, PixelFormat pf, int dbg)
{
    AFGinit(file, pf, dbg);

}

void
AFGvideo::AFGinit(char *file, PixelFormat pf, int dbg)
{
    int i;
    pix_fmt   = pf;
    debug     = dbg;
    hr        = 0;
    hrs       = "";
    cur_loc   = -1;  // cur_loc of 1st frame is 0; So iff frames == 2, loc is 0, 1, 2
    cur_pts   = 0;
    nat_pts   = 0;
    cur_frame = 0;

    // register all formats and codecs
    av_register_all();

    // Open video file
    if(av_open_input_file(&pFormatCtx, file, NULL, 0, NULL)!=0) {
        hr = -1;
        hrs = "Couldn't open file";
        return;
    }

    // Retrieve stream information
    if(av_find_stream_info(pFormatCtx)<0) {
        hr = -1;
        hrs = "Couldn't find stream information";
        return;
    }

    // Dump information about file onto standard error
    if (debug == -2)
        dump_format(pFormatCtx, 0, file, false);

    AVStream *S, **SA = pFormatCtx->streams;

#if 1
    // Show streams
    if (debug == -2) {
        for(i=0; i<pFormatCtx->nb_streams; i++, SA++) {
            S = *SA;
            if (S == NULL) continue;
            AVRational *AVRB = &S->time_base;
            if (AVRB->den)
                base = ((double)AVRB->num)/AVRB->den;
            else
                base = 0;

            start_sec = ((double)S->start_time) * base;
            len_sec   = ((double)S->duration) * base;
            cur_pts   = ((double)S->cur_dts) * base;

            AVRational *AVRR = &S->r_frame_rate;
            if (AVRR->den)
                fps = ((double)AVRR->num)/AVRR->den;
            else
                fps = 0;

            printf("%d: frame_rate %.6f (%d/%d), time_base: %.6f (%d/%d)\n", i,
                   fps,  AVRR->num, AVRR->den,
                   base, AVRB->num, AVRB->den);
            printf("%d: start/len/dts %.6f/%.6f/%.6f\n", i,
                   start_sec, len_sec, cur_pts);
        }
    }
#endif

    // Find the first video stream
    SA = pFormatCtx->streams;
    videoStream=-1;
    for(i=0; i<pFormatCtx->nb_streams; i++, SA++) {
        S = *SA;
        //if(S->codec->codec_type==CODEC_TYPE_VIDEO) {
        if(S->codec->codec_type==AVMEDIA_TYPE_VIDEO) {
            videoStream=i;
            break;
        }
    }
    if(videoStream==-1) {
        hr = -1;
        hrs = "Didn't find a video stream";
        return;
    }
    AVRational *AVRR = &S->r_frame_rate;
    fps = ((double)AVRR->num)/AVRR->den;
    AVRational *AVRB = &S->time_base;
    base = ((double)AVRB->num)/AVRB->den;
    vid_start_sec = ((double)S->start_time) * base;

    // now for microseconds
    start_sec = ((double)pFormatCtx->start_time) / 1000000;
    length = pFormatCtx->duration;

    len_sec   = ((double)length) / 1000000;
    frames = ((int) (len_sec*fps) );
    if (debug < 0) {
        printf("frame_rate: %.6f (%d/%d), time_base: %.6f (%d/%d)\n",
               fps,  AVRR->num, AVRR->den,
               base, AVRB->num, AVRB->den);
        printf("start/len/vid %.6f/%.6f/%.6f\n",
               start_sec, len_sec, vid_start_sec);
    }

// ************************** //
// ms prefers
    first_pts = start_sec;
// or
// virtual dub prefers
//  first_pts = vid_start_sec;//
// ************************** //
    fflush(stdout);


    // Get a pointer to the codec context for the video stream
    pCodecCtx=pFormatCtx->streams[videoStream]->codec;
    width =  pCodecCtx->width;
    height = pCodecCtx->height;

    // Find the decoder for the video stream
    pCodec=avcodec_find_decoder(pCodecCtx->codec_id);
    if(pCodec==NULL) {
        hr = -1;
        hrs = "Codec not found";
        return;
    }

//    if (pCodec->capabilities & CODEC_CAP_TRUNCATED) {
//        printf("Allowing truncated\n");
//        pCodecCtx->flags|=CODEC_FLAG_TRUNCATED;
//    }
    // Open codec
    if(avcodec_open(pCodecCtx, pCodec)<0) {
        hr = -1;
        hrs = "Could not open codec";
        return;
    }

//    cur_dts = pFormatCtx->streams[videoStream]->parser->dts * base;
//    printf("First cur_dts %.3f\n", cur_dts);

    // Hack to correct wrong frame rates that seem to be generated by some 
    // codecs
//fix rvb
//    if(pCodecCtx->frame_rate>1000 && pCodecCtx->frame_rate_base==1)
//        pCodecCtx->frame_rate_base=1000;

    // Allocate video frame
    av_init_packet(&packet);

    pFrame=avcodec_alloc_frame();

    // Allocate an AVFrame structure
    pFrameRGB=avcodec_alloc_frame();
    if(pFrameRGB==NULL) {
        hr = -1;
        hrs = "AVFrame allocation failed";
        return;
    }

    // Determine required buffer size and allocate buffer
    numBytes=avpicture_get_size(pix_fmt, pCodecCtx->width,
        pCodecCtx->height);
    buffer=new uint8_t[numBytes];

    // Assign appropriate parts of buffer to image planes in pFrameRGB
    avpicture_fill((AVPicture *)pFrameRGB, buffer, pix_fmt,
        pCodecCtx->width, pCodecCtx->height);

}

#ifdef _WIN32
__declspec(dllexport)
#endif
AFGvideo::~AFGvideo()
{
    // Free the RGB image
    delete [] buffer;
    av_free(pFrameRGB);

    // Free the YUV frame
    av_free(pFrame);

    // Close the codec
    avcodec_close(pCodecCtx);

    // Close the video file
    av_close_input_file(pFormatCtx);

}

#ifdef _WIN32
__declspec(dllexport)
#endif
int
AFGvideo::Next()
{
    return Next(1);
}

AVFrame *
AFGvideo::Next_PTS()
{
    int frameFinished = 0;
//    int hold;

	av_free_packet(&packet);
	while (av_read_frame(pFormatCtx, &packet) >= 0) {
/*  This code below is what ffplay.c does
		hold = av_read_frame(pFormatCtx, &packet);
		if (hold < 0) {
			if (pFormatCtx->pb.error == 0) {
				SleepEx(100, TRUE);
				continue;
			} else {
				break;
			}
		}
*/
        // is this a packet from the video stream?
        if (packet.stream_index==videoStream) {
            // decode video frame
            //avcodec_decode_video(pCodecCtx, pFrame, &frameFinished, packet.data, packet.size);
            avcodec_decode_video2(pCodecCtx, pFrame, &frameFinished, &packet);
            if (debug > 0) {
                printf("%d: pts %I64u, dts %I64u, pts %.3f, dts %.3f [%.3f], cur/ttl %I64u/%I64u\n",
                       frameFinished,
                       packet.pts, packet.dts,
                       packet.pts * base, packet.dts * base,
                       packet.dts * base - first_pts,
                       cur_loc, frames);
                fflush(stdout);
            }

            // Did we get a video frame?
//            if (frameFinished || packet.pts == packet.dts)
              if (frameFinished)
            {
                if (pFrame->repeat_pict) {
                    printf("OOPS: repeat %d\n", pFrame->repeat_pict);
                }
                cur_loc++;
// this should not be right!! 
                cur_pts = packet.dts * base;
                nat_pts = cur_pts - first_pts;
                return pFrame;
            }
        }
        av_free_packet(&packet);
    }
//    cur_pts = packet.dts * base;
    cur_pts = len_sec + start_sec;
    nat_pts = len_sec;
    return NULL;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
int
AFGvideo::Next(int cnt)
{
    int i = 0;
    AVFrame *Frame;

    if (nat_pts >= len_sec) return 0;

    while ((i < cnt) && (Frame = Next_PTS()) != NULL) {
        if (i++ == 0) {
            // Convert the image from its native format to RGB

			//img_convert((AVPicture *)pFrameRGB, pix_fmt, (AVPicture*)Frame, pCodecCtx->pix_fmt, pCodecCtx->width, pCodecCtx->height);

			struct SwsContext *img_context = sws_getContext(
					pCodecCtx->width, 
					pCodecCtx->height, 
					pCodecCtx->pix_fmt, 
					pCodecCtx->width, 
					pCodecCtx->height,
					pix_fmt, 
					SWS_BICUBIC, NULL, NULL, NULL);

			if(img_context != NULL){
			       	sws_scale(img_context, Frame->data, Frame->linesize, 0, pCodecCtx->height, pFrameRGB->data, pFrameRGB->linesize);
				sws_freeContext(img_context);
			}
	


        } else if (nat_pts >= len_sec) {
            return 0;
        }
    }
    if (Frame == NULL) {
        return 0;
    } else 
        return 1;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
unsigned char *
AFGvideo::buf()
{
    return pFrameRGB->data[0];
}

/*
 * This is currently broken ... seek does not work
 */
#ifdef _WIN32
__declspec(dllexport)
#endif
int
AFGvideo::NEXT(int cnt)
{

    int ret = Next();

    cur_frame += cnt;
    cur_time  = (int64_t) (cur_frame / fps * AV_TIME_BASE);
//    cur_time  = cur_frame / fps / base;
    cur_time += pFormatCtx->start_time;

/*
  #define AVSEEK_FLAG_BACKWARD 1 ///< seek backward
  #define AVSEEK_FLAG_BYTE     2 ///< seeking based on position in bytes
  #define AVSEEK_FLAG_ANY      4 ///< seek to any frame, even non keyframes
*/
    if (av_seek_frame(pFormatCtx, -1, cur_time, 0))
//    if (av_seek_frame(pFormatCtx, videoStream, cur_time, 0) < 0)
    {
        hr = -1;
        hrs = "av_seek_frame failed";
        printf("NEXT: %s\n", hrs); fflush(stdout);
        return ret;
    }
    return ret;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
int
AFGvideo::Seek(double pts)
{
    AVFrame *Frame;

// hmm -> nat_pts already has first_pts subtracted
//    pts += first_pts;

// overstep
    if (nat_pts - pts > ROUND) {
        if (debug > 0) {
            printf("overstep cur %.6f need %.6f\n", nat_pts, pts);
            fflush(stdout);
        }
        return -1;
    }

    if (nat_pts >= len_sec) return 0;

    while ((Frame = Next_PTS()) != NULL) {
        if (abs(pts - nat_pts) < ROUND) {
            if (debug > 0) {
                printf("abs pts %.10g, nat_pts %.10g, eq %d\n",
                       pts, nat_pts, pts == nat_pts);
                fflush(stdout);
            }

            //img_convert((AVPicture *)pFrameRGB, pix_fmt, (AVPicture*)Frame, pCodecCtx->pix_fmt, pCodecCtx->width, pCodecCtx->height);
			
			struct SwsContext *img_context = sws_getContext(
					pCodecCtx->width, 
					pCodecCtx->height, 
					pCodecCtx->pix_fmt, 
					pCodecCtx->width, 
					pCodecCtx->height,
					pix_fmt, 
					SWS_BICUBIC, NULL, NULL, NULL);

			if(img_context != NULL) sws_scale(img_context, Frame->data, Frame->linesize, 0, pCodecCtx->height, pFrameRGB->data, pFrameRGB->linesize);
			

            return 1;
        } else if (pts <= nat_pts) {
            if (debug > 0) {
                printf("<   pts %.10g, nat_pts %.10g, eq %d\n",
                       pts, nat_pts, pts == nat_pts);
                fflush(stdout);
            }

       //  img_convert((AVPicture *)pFrameRGB, pix_fmt, (AVPicture*)Frame, pCodecCtx->pix_fmt, pCodecCtx->width, pCodecCtx->height);

			struct SwsContext *img_context = sws_getContext(
					pCodecCtx->width, 
					pCodecCtx->height, 
					pCodecCtx->pix_fmt, 
					pCodecCtx->width, 
					pCodecCtx->height,
					pix_fmt, 
					SWS_BICUBIC, NULL, NULL, NULL);

			if(img_context != NULL) sws_scale(img_context, Frame->data, Frame->linesize, 0, pCodecCtx->height, pFrameRGB->data, pFrameRGB->linesize);
	

            return 1;
        } else if (nat_pts >= len_sec) {
            return 0;
        }
    }
    if (Frame == NULL) {
        return 0;
    } else 
        return 1;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
int
AFGvideo::Skip(int cnt)
{
    int i = 0;
    AVFrame *Frame;

    if (nat_pts >= len_sec) return 0;

    while ((i < cnt) && (Frame = Next_PTS()) != NULL) {
        i++;
        if (nat_pts >= len_sec) {
            return 0;
        }
    }
    return 1;
}

#ifdef _WIN32
__declspec(dllexport)
#endif
void
AFGvideo::Free()
{
    // Free the packet that was allocated by av_read_frame
    av_free_packet(&packet);
}
