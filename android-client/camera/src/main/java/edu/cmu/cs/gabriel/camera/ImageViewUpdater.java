package edu.cmu.cs.gabriel.camera;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.Log;
import android.widget.ImageView;

import com.google.protobuf.ByteString;

import java.util.function.Consumer;

public class ImageViewUpdater implements Consumer<ByteString> {
    private static final String TAG = "ImageViewUpdater";
    private final ImageView imageView;

    public ImageViewUpdater(ImageView imageView) {
        this.imageView = imageView;
    }

    @Override
    public void accept(ByteString jpegByteString) {
        Bitmap bitmap = BitmapFactory.decodeByteArray(
                jpegByteString.toByteArray(), 0, jpegByteString.size());
        if (bitmap == null) {
            Log.e(TAG, "decodeByteArray returned null!");
            return;
        }

        this.imageView.post(() -> this.imageView.setImageBitmap(bitmap));
    }
}
