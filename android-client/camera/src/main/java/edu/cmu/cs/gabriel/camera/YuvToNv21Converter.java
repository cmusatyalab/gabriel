package edu.cmu.cs.gabriel.camera;

import android.graphics.ImageFormat;

import androidx.camera.core.ImageProxy;

import com.google.protobuf.ByteString;

public class YuvToNv21Converter {
    private byte[] outputBuffer;
    private int pixelCount;

    public synchronized ByteString convertToBuffer(ImageProxy imageProxy) {
        // Wait until we have the first image to allocate this, because width and high might not be
        // exactly what was specified to the camera
        if (this.outputBuffer == null) {
            // From:
            // https://github.com/android/camera-samples/blob/4aac9c7763c285d387194a558416a4458f29e275/CameraUtils/lib/src/main/java/com/example/android/camera/utils/YuvToRgbConverter.kt#L57
            this.pixelCount = imageProxy.getCropRect().width() * imageProxy.getCropRect().height();
            // Bits per pixel is an average for the whole image, so it's useful to compute the size
            // of the full buffer but should not be used to determine pixel offsets
            int pixelSizeBits = ImageFormat.getBitsPerPixel(ImageFormat.YUV_420_888);
            this.outputBuffer = new byte[this.pixelCount * pixelSizeBits / 8];
        }

        YuvToRgbConverter.Companion.imageToByteArray(
                imageProxy, this.outputBuffer, this.pixelCount);
        return ByteString.copyFrom(this.outputBuffer);
    }
}
