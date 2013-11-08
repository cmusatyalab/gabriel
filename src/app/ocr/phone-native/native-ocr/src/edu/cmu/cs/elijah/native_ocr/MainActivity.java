package edu.cmu.cs.elijah.native_ocr;

import java.io.ByteArrayOutputStream;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera;
import android.hardware.Camera.PreviewCallback;
import android.os.Bundle;
import android.util.Log;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.View;
import android.view.View.OnClickListener;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.TextView;

import com.googlecode.tesseract.android.TessBaseAPI;

public class MainActivity extends Activity {
    private static final String LOG_TAG = "MainActivity";
    
    private static final String TESSBASE_PATH = "/mnt/sdcard/tesseract/";
    private static final String DEFAULT_LANGUAGE = "eng";
    private static final String EXPECTED_FILE = TESSBASE_PATH + "tessdata/" + DEFAULT_LANGUAGE
            + ".traineddata";
    
    // UI
    private TextView view_text_output;
    private FrameLayout view_camera;
    private Button button_start;
    private Button button_test_native;
    private Button button_test_offload;
    
    // Camera related
    private Camera mCamera = null;
    private CameraPreview mPreview = null;
    
    // OCR processing
    private TessBaseAPI baseApi = null;
    private boolean OCRRunning = false;
    private ByteArrayOutputStream bufferedOutput;
    private byte[] jpegData;
    private long startedTime;
    private OCRThread ocrThread;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED+
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON+
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        
        setContentView(R.layout.activity_main);
        Log.i(LOG_TAG, "content view set up");
        
        // Initialization
        findViews();
                                       
        // comment out because we are not dealing with real-time image for now
//        bufferedOutput = new ByteArrayOutputStream(1024 * 100);
//        
//        // Create an instance of Camera
//        mCamera = Camera.open();
//        
//        // Create Camera Preview and relate it to the camera
//        mPreview = new CameraPreview(this);
//        mPreview.setCamera(mCamera);
//        
//        Log.i(LOG_TAG, "camera preview set up");
//        
//        view_camera.addView(mPreview);
                
        // Below is where we handle the button click events
        button_start.setOnClickListener(new OnClickListener(){
            @Override
            public void onClick(View v) {
                startRealtime();
                disableButtons();
            }
        });
        button_test_native.setOnClickListener(new OnClickListener(){
            @Override
            public void onClick(View v) {
                disableButtons();
                startBatteryRecording();
                testNative();
            }
        });
        button_test_offload.setOnClickListener(new OnClickListener(){
            @Override
            public void onClick(View v) {
                disableButtons();
                startBatteryRecording();
                testOffload();                
            }
        });
        
    }
    
    private void findViews() {
        view_text_output = (TextView)findViewById(R.id.text_output);
        view_camera = (FrameLayout)findViewById(R.id.camera_preview);
        button_start = (Button)findViewById(R.id.start_button);
        button_test_native = (Button)findViewById(R.id.test_native_button);
        button_test_offload = (Button)findViewById(R.id.test_offload_button);
    }
    
    private void disableButtons() {
        button_start.setEnabled(false);
        button_test_native.setEnabled(false);
        button_test_offload.setEnabled(false);
    }

    @Override
    protected void onDestroy() {
//        stopBatteryRecording();
        if (mCamera != null) {
            mCamera.stopPreview();
            mCamera.release();
        }
        ocrThread.stopOCR();
        super.onDestroy();
        Log.d(LOG_TAG, "prepare to exit activity");
    }
    
    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        // Inflate the menu; this adds items to the action bar if it is present.
        getMenuInflater().inflate(R.menu.main, menu);
        return true;
    }
    
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {                
            finish();
            return true;
        }
            
        return super.onKeyDown(keyCode, event);
    }
    
    private PreviewCallback previewCallback = new PreviewCallback() {
        public void onPreviewFrame(byte[] frame, Camera mCamera) {
            Log.v(LOG_TAG, "calling preview callback");
            if ( OCRRunning ) {
                Log.v(LOG_TAG, "got one frame to process");
                Camera.Parameters parameters = mCamera.getParameters();
                Camera.Size size = parameters.getPreviewSize();
                YuvImage yuvImage = new YuvImage(frame, parameters.getPreviewFormat(), size.width, size.height, null);
                
                // convert YUV image to Bitmap, which is the right format for OCR processing
                yuvImage.compressToJpeg(new Rect(0, 0, yuvImage.getWidth(), yuvImage.getHeight()), 80, bufferedOutput);
                jpegData = bufferedOutput.toByteArray();
                bufferedOutput.reset();
                BitmapFactory.Options bitmapFatoryOptions = new BitmapFactory.Options();
                bitmapFatoryOptions.inPreferredConfig = Bitmap.Config.ARGB_8888;
                Bitmap bmp = BitmapFactory.decodeByteArray(jpegData, 0, jpegData.length, bitmapFatoryOptions);            
                
                // OCR the current frame
                processImageOCR(bmp);
                
                Log.v(LOG_TAG, "processed one frame");
            }
        }
    };
    
    private void testNative() {
        Log.v(LOG_TAG, "About to start offloading");
        ocrThread = new OCRThread(OCRThread.MODE_NATIVE, OCRThread.NETWORK_UNUSED, "", 0, this);
        Log.v(LOG_TAG, "OCR thread created");
        ocrThread.start();
    }
    
    private void testOffload() {
        Log.v(LOG_TAG, "About to start offloading");
        ocrThread = new OCRThread(OCRThread.MODE_OFFLOAD, OCRThread.NETWORK_TCP, "hail.elijah.cs.cmu.edu", 10110, this);
        Log.v(LOG_TAG, "OCR thread created");
        ocrThread.start();
    }
    
    // The entry point of real-time OCR processing
    private void startRealtime() {        
        mPreview.setPreviewCallback(previewCallback);
        Log.i(LOG_TAG, "Added preview callback");
        
        // initialize OCR API
        baseApi = new TessBaseAPI();
        baseApi.init(TESSBASE_PATH, DEFAULT_LANGUAGE);
        baseApi.setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO);
        
        startedTime = System.currentTimeMillis();
        OCRRunning = true;
    }
        
    // The main function of OCR processing
    private void processImageOCR(Bitmap bmp) {
        baseApi.setImage(bmp);
        
        // real OCR recognition here
        Log.d("OCR processing", "OCR starts");
        final String outputText = baseApi.getUTF8Text();
        Log.d("OCR processing", outputText);
        view_text_output.setText(outputText);
    }
    
    /*
     * Recording battery info by sending an intent
     * Current and voltage at the time
     * Sample every 100ms
     */
    Intent batteryRecordingService = null;
    public void startBatteryRecording() {
        BatteryRecordingService.AppName = "native_ocr";
        Log.i("wenluh", "Starting Battery Recording Service");
        batteryRecordingService = new Intent(this, BatteryRecordingService.class);
        startService(batteryRecordingService);
        Log.d("wenluh", "Battery service should be started");
    }

    public void stopBatteryRecording() {
        Log.i("wenluh", "Stopping Battery Recording Service");
        if (batteryRecordingService != null) {
            stopService(batteryRecordingService);
            batteryRecordingService = null;
        }
    }
}
