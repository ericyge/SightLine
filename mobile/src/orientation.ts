// Phone yaw (clockwise degrees) from expo-sensors DeviceMotion, smoothed.
import { DeviceMotion } from 'expo-sensors';
import { angleDiff, cameraHeading } from './guidance';

const SMOOTHING = 0.3;
let yaw: number | null = null;
let subscription: { remove: () => void } | null = null;

/** Ask permission (iOS) and start listening. Returns whether motion data is available. */
export async function startOrientation(): Promise<boolean> {
  if (subscription) return true;
  try {
    const perm = await DeviceMotion.requestPermissionsAsync();
    if (!perm.granted || !(await DeviceMotion.isAvailableAsync())) return false;
    DeviceMotion.setUpdateInterval(100);
    subscription = DeviceMotion.addListener(({ rotation }) => {
      if (!rotation) return;
      const h = cameraHeading(rotation.alpha, rotation.beta, rotation.gamma);
      yaw = yaw == null ? h : (yaw + SMOOTHING * angleDiff(h, yaw) + 360) % 360;
    });
    return true;
  } catch {
    return false;
  }
}

export function stopOrientation() {
  subscription?.remove();
  subscription = null;
  yaw = null;
}

/** Smoothed yaw in degrees, or null without sensor data. */
export const getYaw = () => yaw;
