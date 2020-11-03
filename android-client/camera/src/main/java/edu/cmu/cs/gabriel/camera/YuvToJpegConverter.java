package edu.cmu.cs.gabriel.camera;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Matrix;
import android.graphics.Rect;

import androidx.annotation.NonNull;
import androidx.camera.core.ImageProxy;

import com.google.protobuf.ByteString;

import java.io.ByteArrayOutputStream;

public class YuvToJpegConverter {
    final private static int DEFAULT_JPEG_QUALITY = 67;
    final private YuvToRgbConverter yuvToRgbConverter;
    final private int jpegQuality;
    private Bitmap bitmap;

    public YuvToJpegConverter(Context context, int jpegQuality) {
        this.yuvToRgbConverter = new YuvToRgbConverter(context);
        this.jpegQuality = jpegQuality;
    }

    public YuvToJpegConverter(Context context) {
        this(context, DEFAULT_JPEG_QUALITY);
    }

    public synchronized ByteString convertToJpeg(@NonNull ImageProxy imageProxy) {
        // Wait until we have the first image to allocate this, because width and high might not be
        // exactly what was specified to the camera
        if (bitmap == null) {
            bitmap = Bitmap.createBitmap(
                    imageProxy.getWidth(), imageProxy.getHeight(), Bitmap.Config.ARGB_8888);
        }

        yuvToRgbConverter.yuvToRgb(imageProxy, bitmap);

        // Images are passed to this method without being rotated
        Matrix matrix = new Matrix();
        matrix.postRotate(imageProxy.getImageInfo().getRotationDegrees());

        // rotatedBitmap must be its own variable because the dimensions are different
        // from those of bitmap
        Rect cropRect = imageProxy.getCropRect();
        Bitmap rotatedBitmap = Bitmap.createBitmap(
                bitmap, cropRect.left, cropRect.top, cropRect.width(), cropRect.height(), matrix,
                true);

        ByteArrayOutputStream byteArrayOutputStream = new ByteArrayOutputStream();
        rotatedBitmap.compress(Bitmap.CompressFormat.JPEG, this.jpegQuality, byteArrayOutputStream);

        return ByteString.copyFrom(byteArrayOutputStream.toByteArray());
    }
}
