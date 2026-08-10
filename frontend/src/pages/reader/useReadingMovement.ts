import { useCallback, useRef } from 'react';

/**
 * Foliate emits relocate events for layout/reflow as well as user navigation.
 * This gate records explicit reading movement so programmatic restore/reflow
 * can never become a persisted position by accident.
 */
export function useReadingMovement() {
  const movementRef = useRef(false);
  const markReadingMovement = useCallback(() => {
    movementRef.current = true;
  }, []);
  const resetReadingMovement = useCallback(() => {
    movementRef.current = false;
  }, []);
  return { movementRef, markReadingMovement, resetReadingMovement };
}
