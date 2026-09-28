import { useEffect, useState } from 'react';

/** The two screens of the dashboard: the Data screen first, the state of the API second. */
export type Screen = 'data' | 'status';

/** The screen an address names: #/status for the state of the API, anything else for Data. */
export function screenOf(hash: string): Screen {
  return hash === '#/status' ? 'status' : 'data';
}

/** The screen of the address, kept in step with the links and the back button. */
export function useScreen(): Screen {
  const [screen, setScreen] = useState<Screen>(() => screenOf(window.location.hash));
  useEffect(() => {
    const follow = () => {
      setScreen(screenOf(window.location.hash));
    };
    window.addEventListener('hashchange', follow);
    return () => {
      window.removeEventListener('hashchange', follow);
    };
  }, []);
  return screen;
}
