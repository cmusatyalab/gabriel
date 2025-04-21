// This class is based on the following codelab:
// https://codelabs.developers.google.com/codelabs/camerax-getting-started

package edu.cmu.cs.gabriel.camera;

import android.Manifest;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.Log;
import android.util.Size;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;
import android.view.WindowManager;
import android.widget.Toast;

import androidx.activity.result.ActivityResultCallback;
import androidx.activity.result.contract.ActivityResultContracts.RequestPermission;
import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.camera.core.Camera;
import androidx.camera.core.CameraControl;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.FocusMeteringAction;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.core.MeteringPoint;
import androidx.camera.core.MeteringPointFactory;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;

import androidx.camera.view.PreviewView;
import androidx.core.content.ContextCompat;
import androidx.activity.result.ActivityResultLauncher;

import com.google.common.util.concurrent.ListenableFuture;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.util.AbstractMap;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class CameraCapture {
    private static final String TAG = "CameraCapture";
    private static final String REQUIRED_PERMISSION = Manifest.permission.CAMERA;
    private static final CameraSelector DEFAULT_SELECTOR = CameraSelector.DEFAULT_BACK_CAMERA;
    private static final boolean DEFAULT_ENABLE_TORCH = false;
    private final AppCompatActivity activity;
    private final ExecutorService cameraExecutor;
    private List<Map.Entry<Long, String>> recordedFrameData;
    private int nextFrameIndexToCheck = 0;
    private long recordReplayTimeOffset = 0;
    private final String replayFolder;
    private final boolean useRecordedFrames;
    private boolean replayDone = false;

    public CameraCapture(
            AppCompatActivity activity, BitmapAnalyzer analyzer, int width, int height) {
        this(activity, analyzer, width, height, null);
    }

    public CameraCapture(
            AppCompatActivity activity, BitmapAnalyzer analyzer, int width, int height,
            PreviewView viewFinder) {
        this(activity, analyzer, width, height, viewFinder, DEFAULT_SELECTOR);
    }

    public CameraCapture(
            AppCompatActivity activity, BitmapAnalyzer analyzer, int width, int height,
            PreviewView viewFinder, CameraSelector cameraSelector) {
        this(activity,analyzer, width, height, viewFinder, cameraSelector, DEFAULT_ENABLE_TORCH);
    }

    public CameraCapture(
            AppCompatActivity activity, BitmapAnalyzer analyzer, int width, int height,
            PreviewView viewFinder, CameraSelector cameraSelector, boolean enableTorch) {
        this(activity,analyzer, width, height, viewFinder, cameraSelector, enableTorch,
                null, null);
    }

    public CameraCapture(
            AppCompatActivity activity, BitmapAnalyzer analyzer, int width, int height,
            PreviewView viewFinder, CameraSelector cameraSelector, boolean enableTorch,
            String replayFolder, String recordedLogFile) {
        this.activity = activity;
        if (replayFolder == null || recordedLogFile == null ||
                replayFolder.isEmpty() || recordedLogFile.isEmpty()) {
            this.replayFolder = null;
            useRecordedFrames = false;
        } else {
            this.replayFolder = replayFolder;
            nextFrameIndexToCheck = 0;
            useRecordedFrames = true;
            readRecordedLog(replayFolder + "/" + recordedLogFile);
        }

        int permission = ContextCompat.checkSelfPermission(this.activity, REQUIRED_PERMISSION);
        if (permission == PackageManager.PERMISSION_GRANTED) {
            this.startCamera(analyzer, width, height, cameraSelector, viewFinder, enableTorch);
        } else {
            ActivityResultCallback<Boolean> activityResultCallback = isGranted -> {
                if (isGranted) {
                    CameraCapture.this.startCamera(analyzer, width, height, cameraSelector,
                            viewFinder, enableTorch);
                } else {
                    Toast.makeText(this.activity,
                            "The user denied the camera permission.", Toast.LENGTH_LONG).show();
                }
            };

            ActivityResultLauncher<String> requestPermissionLauncher =
                    this.activity.registerForActivityResult(
                            new RequestPermission(), activityResultCallback);

            requestPermissionLauncher.launch(REQUIRED_PERMISSION);
        }

        this.cameraExecutor = Executors.newSingleThreadExecutor();
        activity.getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    private void readRecordedLog(String logFilePath) {
        BufferedReader reader = null;
        try {
            reader = new BufferedReader(new InputStreamReader(this.activity.getAssets().open(logFilePath)));
            recordedFrameData = new ArrayList<>();
            String logLine;
            while ((logLine = reader.readLine()) != null && !logLine.isEmpty() ) {
                recordedFrameData.add(
                        new AbstractMap.SimpleEntry<>(Long.parseLong(logLine.split(",")[0]),
                                logLine.split(",")[1])
                );
            }
        } catch (IOException e) {
            Log.e(TAG, "Failed to open log file.");
        } finally {
            if (reader != null) {
                try {
                    reader.close();
                } catch (IOException e) {
                    Log.e(TAG, "Failed to close log file.");
                }
            }
        }
    }

    private String chooseFrameToReplay(long curLocalTime) {
        if (nextFrameIndexToCheck == 0) {
            long firstFrameTimestamp = recordedFrameData.get(0).getKey();
            recordReplayTimeOffset = curLocalTime - firstFrameTimestamp;
            nextFrameIndexToCheck++;
            return recordedFrameData.get(0).getValue();
        }
        if (nextFrameIndexToCheck >= recordedFrameData.size() || nextFrameIndexToCheck < 0) {
            nextFrameIndexToCheck = -1;
            return "";
        }
        long curSyncTime = curLocalTime - recordReplayTimeOffset;
        if (curSyncTime < recordedFrameData.get(nextFrameIndexToCheck).getKey()) {
            Log.w(TAG, "A frame is requested to be processed earlier than it arrives during recording. " +
                    "This should rarely happen. We block till the time the frame should arrive.");
            try {
                Thread.sleep(recordedFrameData.get(nextFrameIndexToCheck).getKey() - curSyncTime);
            } catch (InterruptedException e) {
                e.printStackTrace();
            }
            nextFrameIndexToCheck++;
            return recordedFrameData.get(nextFrameIndexToCheck - 1).getValue();
        }
        // Same policy as "keep latest frame"
        do {
            nextFrameIndexToCheck++;
        } while (nextFrameIndexToCheck < recordedFrameData.size() &&
                curSyncTime >= recordedFrameData.get(nextFrameIndexToCheck).getKey());
        return recordedFrameData.get(nextFrameIndexToCheck - 1).getValue();
    }

    private void startCamera(
            BitmapAnalyzer analyzer, int width, int height, CameraSelector cameraSelector,
            PreviewView viewFinder, boolean enableTorch) {

        ImageAnalysis.Analyzer imageAnalyzer = new ImageAnalysis.Analyzer() {
            @Override
            public void analyze(@NonNull ImageProxy image) {
                Bitmap cameraBitmap = image.toBitmap();
                if (useRecordedFrames && !replayDone) {
                    // TODO: Measure JPG loading time; maybe compensate for it by skipping image proxy
                    //       to bitmap conversion
                    long curLocalTime = System.currentTimeMillis();
                    String frameToLoad = chooseFrameToReplay(curLocalTime);

                    if (frameToLoad.isEmpty()) {
                        // Reaching the end of the recorded frames
                        replayDone = true;
                    } else {
                        try {
                            InputStream inputStream = activity.getAssets().open(replayFolder + "/" + frameToLoad);
                            cameraBitmap = BitmapFactory.decodeStream(inputStream);
                            inputStream.close();
                        } catch (IOException e) {
                            Log.w(TAG, "Failed to load frame: " + frameToLoad);
                            image.close();
                            return;
                        }
                    }
                }
                boolean started = analyzer.analyze(cameraBitmap, recordReplayTimeOffset, replayDone);
                if (!started && !replayDone) {
                    // Hold back because MainActivity is not ready to run image analysis yet
                    nextFrameIndexToCheck = 0;
                }

                // The image has either been processed or skipped. It is therefore safe to close the image.
                image.close();
            }
        };

        ListenableFuture<ProcessCameraProvider> cameraProviderFuture =
                ProcessCameraProvider.getInstance(this.activity);
        cameraProviderFuture.addListener(() -> {
            try {
                ProcessCameraProvider cameraProvider = cameraProviderFuture.get();

                ImageAnalysis imageAnalysis = new ImageAnalysis.Builder()
                        .setTargetResolution(new Size(width, height))
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build();
                imageAnalysis.setAnalyzer(this.cameraExecutor, imageAnalyzer);

                // Unbind use cases before rebinding
                cameraProvider.unbindAll();

                if (viewFinder == null) {
                    // Bind use cases to camera
                    cameraProvider.bindToLifecycle(this.activity, cameraSelector, imageAnalysis);
                } else {
                    Preview preview = new Preview.Builder().build();
                    preview.setSurfaceProvider(viewFinder.getSurfaceProvider());
                    Camera camera = cameraProvider.bindToLifecycle(
                            this.activity, cameraSelector, imageAnalysis, preview);

                    camera.getCameraControl().enableTorch(enableTorch);

                    // Begin pinch to zoom and tap to focus
                    CameraControl cameraControl = camera.getCameraControl();
                    ScaleGestureDetector.SimpleOnScaleGestureListener simpleOnScaleGestureListener =
                            new ScaleGestureDetector.SimpleOnScaleGestureListener() {
                                @Override
                                public boolean onScale(ScaleGestureDetector detector) {
                                    float currentZoomRatio = camera.getCameraInfo()
                                            .getZoomState().getValue().getZoomRatio();

                                    float delta = detector.getScaleFactor();

                                    cameraControl.setZoomRatio(currentZoomRatio * delta);

                                    return true;
                                }
                            };
                    ScaleGestureDetector scaleGestureDetector = new ScaleGestureDetector(
                            this.activity, simpleOnScaleGestureListener);

                    // Based on https://stackoverflow.com/a/59087108/859277
                    viewFinder.setOnTouchListener(new View.OnTouchListener() {
                        @Override
                        public boolean onTouch(View view, MotionEvent motionEvent) {
                            scaleGestureDetector.onTouchEvent(motionEvent);
                            if (motionEvent.getAction() != MotionEvent.ACTION_UP) {
                                return true;
                            }

                            MeteringPointFactory meteringPointFactory =
                                    viewFinder.getMeteringPointFactory();
                            MeteringPoint meteringPoint = meteringPointFactory.createPoint(
                                    motionEvent.getX(), motionEvent.getY());
                            FocusMeteringAction action =
                                    new FocusMeteringAction.Builder(meteringPoint).build();
                            cameraControl.startFocusAndMetering(action);

                            return true;
                        }
                    });
                    // End pinch to zoom and tap to focus
                }
            } catch (ExecutionException | InterruptedException e) {
                Log.e(TAG, "Could not setup camera", e);
            }
        }, ContextCompat.getMainExecutor(this.activity));
    }

    public void shutdown() {
        this.cameraExecutor.shutdown();
    }
}
