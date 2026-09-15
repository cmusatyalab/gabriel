import com.google.protobuf.gradle.id
import com.google.protobuf.gradle.proto

plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.protobuf)
}

android {
    namespace = "edu.cmu.cs.gabriel.client"
    compileSdk = 37

    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    // GabrielClient logs via android.util.Log, which the unit test
    // classpath's stub android.jar throws UnsupportedOperationException
    // from by default (it has no real Android runtime to log to). Returning
    // default values instead lets a plain Log.w(...) silently no-op rather
    // than crashing every test that hits a code path with logging in it.
    testOptions {
        unitTests {
            isReturnDefaultValues = true
        }
    }

    // The Gabriel protocol buffers live in the top-level protocol/ directory
    // so they can be shared across every language's client, rather than
    // being duplicated here.
    sourceSets {
        getByName("main") {
            proto {
                srcDir("../../protocol/proto")
            }
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

protobuf {
    protoc {
        artifact = libs.protobuf.protoc.get().toString()
    }
    plugins {
        id("grpc") {
            artifact = libs.grpc.plugin.java.get().toString()
        }
        id("grpckt") {
            artifact = libs.grpc.plugin.kotlin.get().toString() + ":jdk8@jar"
        }
    }
    generateProtoTasks {
        all().forEach { task ->
            task.builtins {
                id("java") {
                    option("lite")
                }
                id("kotlin") {
                    option("lite")
                }
            }
            task.plugins {
                id("grpc") {
                    option("lite")
                }
                id("grpckt") {
                    option("lite")
                }
            }
        }
    }
}

dependencies {
    // These types (Flow, StateFlow, generated protobuf/gRPC classes) appear
    // in GabrielClient's public API, so consumers need them on their own
    // compile classpath too.
    api(libs.kotlinx.coroutines.core)
    api(libs.grpc.kotlin.stub)
    api(libs.grpc.protobuf.lite)
    api(libs.grpc.stub)
    api(libs.protobuf.kotlin.lite)

    compileOnly(libs.javax.annotation.api)

    testImplementation(libs.junit)
    // Real (not in-process) transport, since RealServerCategory tests talk
    // to an actual server subprocess over a real socket.
    testImplementation(libs.grpc.okhttp)
}

// RealServerCategory tests launch server/main.py as a subprocess, so they
// need a Python 3 interpreter with the server's dependencies installed (see
// server/pyproject.toml) and are excluded from the default unit test run.
// Run them explicitly with `./gradlew :gabriel-client:integrationTest`.
tasks.withType<Test>().configureEach {
    systemProperty("gabriel.server.mainPy", rootProject.file("../server/main.py").absolutePath)
    System.getenv("GABRIEL_TEST_PYTHON")?.let { systemProperty("gabriel.server.python", it) }
}

// AGP registers testDebugUnitTest lazily via its variant callbacks, which
// run after this script's top-level statements, so it doesn't exist in the
// task container yet at this point unless we defer until after evaluation.
afterEvaluate {
    tasks.named<Test>("testDebugUnitTest") {
        useJUnit { excludeCategories("edu.cmu.cs.gabriel.client.RealServerCategory") }
    }

    tasks.register<Test>("integrationTest") {
        group = "verification"
        description = "Runs tests against a real Gabriel server subprocess (needs Python 3 " +
            "with the server's dependencies installed)."
        val unitTest = tasks.named<Test>("testDebugUnitTest").get()
        testClassesDirs = unitTest.testClassesDirs
        classpath = unitTest.classpath
        systemProperty("gabriel.server.mainPy", rootProject.file("../server/main.py").absolutePath)
        System.getenv("GABRIEL_TEST_PYTHON")?.let { systemProperty("gabriel.server.python", it) }
        useJUnit { includeCategories("edu.cmu.cs.gabriel.client.RealServerCategory") }
    }
}
