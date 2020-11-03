// This class is based on the following codelab:
// https://codelabs.developers.google.com/codelabs/camerax-getting-started

package edu.cmu.cs.gabriel.camera;

import android.Manifest;
import android.content.pm.PackageManager;
import android.util.Log;
import android.util.Size;
import android.view.WindowManager;
import android.widget.Toast;

import androidx.activity.result.ActivityResultCallback;
import androidx.activity.result.contract.ActivityResultContracts.RequestPermission;
import androidx.appcompat.app.AppCompatActivity;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;

import androidx.camera.view.PreviewView;
import androidx.core.content.ContextCompat;
import androidx.activity.result.ActivityResultLauncher;

import com.google.common.util.concurrent.ListenableFuture;

import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class CameraCapture {
    private static final String TAG = "CameraCapture";
    private static final String REQUIRED_PERMISSION = Manifest.permission.CAMERA;
    private static final CameraSelector CAMERA_SELECTOR = CameraSelector.DEFAULT_BACK_CAMERA;
    private final AppCompatActivity activity;
    private final ExecutorService cameraExecutor;

    public CameraCapture(
            AppCompatActivity activity, ImageAnalysis.Analyzer analyzer, int width, int height) {
        this(activity, analyzer, width, height, null);
    }

    public CameraCapture(
            AppCompatActivity activity, ImageAnalysis.Analyzer analyzer, int width, int height,
            PreviewView viewFinder) {
        this.activity = activity;

        int permission = ContextCompat.checkSelfPermission(this.activity, REQUIRED_PERMISSION);
        if (permission == PackageManager.PERMISSION_GRANTED) {
            this.startCamera(analyzer, width, height, viewFinder);
        } else {
            ActivityResultCallback<Boolean> activityResultCallback = isGranted -> {
                if (isGranted) {
                    CameraCapture.this.startCamera(analyzer, width, height, viewFinder);
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

    private void startCamera(
            ImageAnalysis.Analyzer analyzer, int width, int height, PreviewView viewFinder) {
        ListenableFuture<ProcessCameraProvider> cameraProviderFuture =
                ProcessCameraProvider.getInstance(this.activity);
        cameraProviderFuture.addListener(() -> {
            try {
                ProcessCameraProvider cameraProvider = cameraProviderFuture.get();

                ImageAnalysis imageAnalysis = new ImageAnalysis.Builder()
                        .setTargetResolution(new Size(width, height))
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build();
                imageAnalysis.setAnalyzer(this.cameraExecutor, analyzer);

                // Unbind use cases before rebinding
                cameraProvider.unbindAll();

                if (viewFinder == null) {
                    // Bind use cases to camera
                    cameraProvider.bindToLifecycle(this.activity, CAMERA_SELECTOR, imageAnalysis);
                } else {
                    Preview preview = new Preview.Builder().build();
                    preview.setSurfaceProvider(viewFinder.getSurfaceProvider());
                    cameraProvider.bindToLifecycle(
                            this.activity, CAMERA_SELECTOR, imageAnalysis, preview);
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
