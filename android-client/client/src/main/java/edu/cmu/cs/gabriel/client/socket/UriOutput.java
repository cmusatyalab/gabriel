package edu.cmu.cs.gabriel.client.socket;

class UriOutput {
    private final OutputType outputType;
    private final String output;

    UriOutput(OutputType outputType, String output) {
        this.outputType = outputType;
        this.output = output;
    }

    UriOutput(OutputType outputType) {
        this(outputType, null);
    }

    enum OutputType {
        VALID,
        CLEARTEXT_WITHOUT_PERMISSION,
        INVALID
    }

    OutputType getOutputType() {
        return outputType;
    }

    String getOutput() {
        if (this.outputType != OutputType.VALID) {
            throw new AssertionError("Only Valid URIs have outputs");
        }
        return output;
    }
}