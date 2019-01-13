package edu.cmu.cs.gabrielclient;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaPlayer;
import android.net.Uri;
import android.os.Handler;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.util.Base64;
import android.util.Log;
import android.view.View;
import android.widget.ImageView;
import android.widget.MediaController;
import android.widget.TextView;
import android.widget.VideoView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.HashMap;
import java.util.Locale;
import java.util.Timer;
import java.util.TimerTask;

public class InstructionViewer implements TextToSpeech.OnInitListener {
    private static final String LOG_TAG = InstructionViewer.class.getSimpleName();
    public ImageView imageView = null;
    public VideoView videoView = null;
    public TextView subtitleView = null;
    // animation
    public Timer timer = null;
    private TextToSpeech tts = null;
    private MediaController mediaController = null;
    private Context appContext = null;
    private Bitmap[] animationFrames = new Bitmap[10];
    private int[] animationPeriods = new int[10]; // how long each frame is shown, in millisecond
    private int animationDisplayIdx = -1;
    private int nAnimationFrames = -1;
    private Handler uiHandler = new Handler();
    private Runnable updateAnimation = new Runnable() {
        public void run() {
//            Log.d(LOG_TAG, "update animation run");
            animationDisplayIdx = (animationDisplayIdx + 1) % nAnimationFrames;
            setImageInst(animationFrames[animationDisplayIdx]);
            if (imageView != null) {
                uiHandler.postDelayed(this, animationPeriods[animationDisplayIdx]);
            }
        }
    };

    public InstructionViewer(Context appContext, ImageView imageView, VideoView videoView, TextView subtitleView) {
        this.appContext = appContext;
        this.imageView = imageView;
        this.videoView = videoView;
        this.subtitleView = subtitleView;
        this.tts = new TextToSpeech(this.appContext, this);
        this.mediaController = new MediaController(this.appContext);
    }

    /**
     * Parse instruction
     *
     * @param result
     */
    public void parseAndSetInstruction(String result) {
        JSONObject resultJSON = null;
        try {
            resultJSON = new JSONObject(result);
        } catch (JSONException e) {
            Log.w(LOG_TAG, "Result message not in correct JSON format");
            Log.w(LOG_TAG, result);
        }
        String speechFeedback = "";
        Bitmap imageFeedback = null;
        String videoFeedback = null;
        JSONArray animationFeedback = null;

        // speech guidance
        try {
            speechFeedback = resultJSON.getString("speech");
        } catch (JSONException e) {
            Log.v(LOG_TAG, "no speech guidance found");
        }
        // image guidance
        try {
            String imageFeedbackString = resultJSON.getString("image");
            byte[] data = Base64.decode(imageFeedbackString.getBytes(), Base64.DEFAULT);
            imageFeedback = BitmapFactory.decodeByteArray(data, 0, data.length);
        } catch (JSONException e) {
            Log.v(LOG_TAG, "no image guidance found");
        }

        // video guidance
        try {
            videoFeedback = resultJSON.getString("video");
        } catch (JSONException e) {
            Log.v(LOG_TAG, "no video guidance found");
        }

        // animation guidance
        try {
            animationFeedback = resultJSON.getJSONArray("animation");
        } catch (JSONException e) {
            Log.v(LOG_TAG, "no animation guidance found");
        }
        setInst(speechFeedback, imageFeedback, videoFeedback, animationFeedback);

//        Message msg = Message.obtain();
//        msg.what = NetworkProtocol.NETWORK_RET_TOKEN;
//        receivedPacketInfo.setGuidanceDoneTime(System.currentTimeMillis());
//        msg.obj = receivedPacketInfo;
//        try {
//            tokenController.tokenHandler.sendMessage(msg);
//        } catch (NullPointerException e) {
//            // might happen because token controller might have been terminated
//        }
    }

    public void ttsSpeak(String speechFeedback) {
        Log.d(LOG_TAG, "tts to be played: " + speechFeedback);
        // TODO: check if tts is playing something else
        tts.setSpeechRate(1.0f);
        String[] splitMSGs = speechFeedback.split("\\.");
        HashMap<String, String> map = new HashMap<String, String>();
        map.put(TextToSpeech.Engine.KEY_PARAM_UTTERANCE_ID, "unique");

        if (splitMSGs.length == 1)
            tts.speak(splitMSGs[0].trim(), TextToSpeech.QUEUE_FLUSH, map); // the only sentence
        else {
            tts.speak(splitMSGs[0].trim(), TextToSpeech.QUEUE_FLUSH, null); // the first sentence
            for (int i = 1; i < splitMSGs.length - 1; i++) {
                tts.playSilence(350, TextToSpeech.QUEUE_ADD, null); // add pause for every period
                tts.speak(splitMSGs[i].trim(), TextToSpeech.QUEUE_ADD, null);
            }
            tts.playSilence(350, TextToSpeech.QUEUE_ADD, null);
            tts.speak(splitMSGs[splitMSGs.length - 1].trim(), TextToSpeech.QUEUE_ADD, map); // the last
            // sentence
        }
    }

    public void setSpeechInst(String speechFeedback) {
        if (tts != null) {
            ttsSpeak(speechFeedback);
        }
        subtitleView.setText(speechFeedback);
    }

    public void setImageInst(Bitmap imageFeedback) {
        Log.d(LOG_TAG, "update image view");
        imageView.setVisibility(View.VISIBLE);
        videoView.setVisibility(View.GONE);
        imageView.setImageBitmap(imageFeedback);
    }

    public void setVideoInst(String videoFeedback) {
        imageView.setVisibility(View.GONE);
        videoView.setVisibility(View.VISIBLE);
        videoView.setVideoURI(Uri.parse(videoFeedback));
        videoView.setMediaController(mediaController);
        //Loop Video
        videoView.setOnCompletionListener(new MediaPlayer.OnCompletionListener() {
            public void onCompletion(MediaPlayer mp) {
                videoView.start();
            }
        });
        videoView.start();
    }

    public void setAnimationInst(JSONArray animationArray) {
        nAnimationFrames = animationArray.length();
        try {
            for (int i = 0; i < nAnimationFrames; i++) {
                JSONArray frameArray = animationArray.getJSONArray(i);
                String animationFrameString = frameArray.getString(0);
                byte[] data = Base64.decode(animationFrameString.getBytes(), Base64.DEFAULT);
                animationFrames[i] = BitmapFactory.decodeByteArray(data, 0, data.length);
                animationPeriods[i] = frameArray.getInt(1);
            }
            animationDisplayIdx = -1;
            uiHandler.postDelayed(updateAnimation, 4000);
//            if (timer == null) {
//                timer = new Timer();
//                timer.schedule(new animationTask(), 0);
//            }
        } catch (JSONException e) {
            Log.w(LOG_TAG, "Invalid Animation.");
        }
    }

    public void setInst(String speechFeedback, Bitmap imageFeedback, String videoFeedback, JSONArray
            animationFeedback) {
        if (speechFeedback != null) {
            setSpeechInst(speechFeedback);
        }
        if (imageFeedback != null) {
            setImageInst(imageFeedback);
        }
        if (videoFeedback != null) {
            setVideoInst(videoFeedback);
        }
        if (animationFeedback != null) {
            setAnimationInst(animationFeedback);
        }
    }

    /**************** TextToSpeech.OnInitListener ***************/
    public void onInit(int status) {
        if (status == TextToSpeech.SUCCESS) {
//            if (tts == null) {
//                tts = new TextToSpeech(this.appContext, this);
//            }
            int result = tts.setLanguage(Locale.US);
            if (result == TextToSpeech.LANG_MISSING_DATA || result == TextToSpeech.LANG_NOT_SUPPORTED) {
                Log.e(LOG_TAG, "Language is not available.");
            }
            int listenerResult = tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override
                public void onDone(String utteranceId) {
                    Log.v(LOG_TAG, "progress on Done " + utteranceId);
                }

                @Override
                public void onError(String utteranceId) {
                    Log.v(LOG_TAG, "progress on Error " + utteranceId);
                }

                @Override
                public void onStart(String utteranceId) {
                    Log.v(LOG_TAG, "progress on Start " + utteranceId);
                }
            });
            if (listenerResult != TextToSpeech.SUCCESS) {
                Log.e(LOG_TAG, "failed to add utterance progress listener");
            }
        } else {
            // Initialization failed.
            Log.e(LOG_TAG, "Could not initialize TextToSpeech.");
        }
    }

    public void cancelAnimationTimer() {
        if (timer != null) {
            timer.cancel();
            timer = null;
        }
    }

    public void close() {
        this.cancelAnimationTimer();
        this.uiHandler.removeCallbacks(updateAnimation);
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
    }

    private class animationTask extends TimerTask {
        @Override
        public void run() {
            Log.v(LOG_TAG, "Running timer task");
        }
    }
}
