package edu.cmu.cs.roundtrip;

import android.os.Bundle;
import android.util.Log;
import android.widget.ImageView;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.camera.core.ImageAnalysis;
import androidx.camera.core.ImageProxy;
import androidx.camera.view.PreviewView;

import com.google.protobuf.ByteString;

import java.util.function.Consumer;

import edu.cmu.cs.gabriel.camera.CameraCapture;
import edu.cmu.cs.gabriel.camera.YuvToJPEGConverter;
import edu.cmu.cs.gabriel.camera.ImageViewUpdater;
import edu.cmu.cs.gabriel.client.comm.ServerComm;
import edu.cmu.cs.gabriel.client.results.ErrorType;
import edu.cmu.cs.gabriel.protocol.Protos;
import edu.cmu.cs.gabriel.protocol.Protos.InputFrame;
import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;

public class GabrielActivity extends AppCompatActivity {
    private static final String TAG = "GabrielActivity";
    private static final String SOURCE = "roundtrip";
    private static final int PORT = 9099;
    private static final int WIDTH = 640;
    private static final int HEIGHT = 480;

    private ServerComm serverComm;
    private YuvToJPEGConverter yuvToJPEGConverter;
    private CameraCapture cameraCapture;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        setContentView(R.layout.activity_gabriel);

        PreviewView viewFinder = findViewById(R.id.viewFinder);
        ImageView imageView = findViewById(R.id.imageView);
        ImageViewUpdater imageViewUpdater = new ImageViewUpdater(imageView);

        Consumer<ResultWrapper> consumer = resultWrapper -> {
            ResultWrapper.Result result = resultWrapper.getResults(0);
            ByteString jpegByteString = result.getPayload();

            imageViewUpdater.accept(jpegByteString);
        };

        Consumer<ErrorType> onDisconnect = errorType -> {
            Log.e(TAG, "Disconnect Error:" + errorType.name());
            finish();
        };

        serverComm = ServerComm.createServerComm(
                consumer, BuildConfig.GABRIEL_HOST, PORT, getApplication(), onDisconnect);

        yuvToJPEGConverter = new YuvToJPEGConverter(this);
        cameraCapture = new CameraCapture(this, analyzer, WIDTH, HEIGHT, viewFinder);
    }

    final private ImageAnalysis.Analyzer analyzer = new ImageAnalysis.Analyzer() {
        @Override
        public void analyze(@NonNull ImageProxy image) {
            serverComm.sendSupplier(() -> {
                ByteString jpegByteString = yuvToJPEGConverter.convert(image);

                return InputFrame.newBuilder()
                        .setPayloadType(Protos.PayloadType.IMAGE)
                        .addPayloads(jpegByteString)
                        .build();
            }, SOURCE, false);

            image.close();
        }
    };

    @Override
    protected void onDestroy() {
        super.onDestroy();
        cameraCapture.shutdown();
    }
}
