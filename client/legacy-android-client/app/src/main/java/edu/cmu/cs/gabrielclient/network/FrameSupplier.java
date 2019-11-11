package edu.cmu.cs.gabrielclient.network;

import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera.Size;

import com.google.protobuf.Any;
import com.google.protobuf.ByteString;

import java.io.ByteArrayOutputStream;

import edu.cmu.cs.gabriel.client.function.Supplier;
import edu.cmu.cs.gabriel.protocol.Protos.PayloadType;
import edu.cmu.cs.gabriel.protocol.Protos.FromClient;
import edu.cmu.cs.gabrielclient.Const;
import edu.cmu.cs.gabriel.instruction.Protos.EngineFields;
import edu.cmu.cs.gabrielclient.GabrielClientActivity;

public class FrameSupplier implements Supplier<FromClient.Builder> {
    private GabrielClientActivity gabrielClientActivity;
    private InstructionComm instructionComm;

    public FrameSupplier(
            GabrielClientActivity gabrielClientActivity, InstructionComm instructionComm) {
        this.gabrielClientActivity = gabrielClientActivity;
        this.instructionComm = instructionComm;
    }

    private static byte[] createFrameData(EngineInput engineInput) {
        Size cameraImageSize = engineInput.parameters.getPreviewSize();
        YuvImage image = new YuvImage(
                engineInput.frame, engineInput.parameters.getPreviewFormat(), cameraImageSize.width,
                cameraImageSize.height, null);
        ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
        // chooses quality 67 and it roughly matches quality 5 in avconv
        image.compressToJpeg(new Rect(
                0, 0, image.getWidth(), image.getHeight()), 67, tmpBuffer);
        return tmpBuffer.toByteArray();
    }

    private static FromClient.Builder convertEngineInput(
            EngineInput engineInput, EngineFields engineFields) {
        byte[] frame = FrameSupplier.createFrameData(engineInput);

        FromClient.Builder fromClientBuilder = FromClient.newBuilder();
        fromClientBuilder.setPayloadType(PayloadType.IMAGE);
        fromClientBuilder.setEngineName(Const.ENGINE_NAME);
        fromClientBuilder.setPayload(ByteString.copyFrom(frame));

        fromClientBuilder.setEngineFields(FrameSupplier.pack(engineFields));

        return fromClientBuilder;
    }

    public FromClient.Builder get() {
        EngineInput engineInput = this.gabrielClientActivity.getEngineInput();
        if (engineInput == null) {
            return null;
        }

        EngineFields engineFields = this.instructionComm.getEngingFields();
        return FrameSupplier.convertEngineInput(engineInput, engineFields);
    }

    // Based on
    // https://github.com/protocolbuffers/protobuf/blob/master/src/google/protobuf/compiler/java/java_message.cc#L1387
    private static Any pack(EngineFields engineFields) {
        return Any.newBuilder()
                .setTypeUrl("type.googleapis.com/instruction.EngineFields")
                .setValue(engineFields.toByteString())
                .build();
    }
}
