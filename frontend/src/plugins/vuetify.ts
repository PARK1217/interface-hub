import { createVuetify } from 'vuetify';
import { aliases, mdi } from 'vuetify/iconsets/mdi';

export default createVuetify({
  icons: { defaultSet: 'mdi', aliases, sets: { mdi } },
  theme: {
    defaultTheme: 'noahub',
    themes: {
      noahub: {
        dark: false,
        colors: {
          primary: '#1F3A93',
          secondary: '#4B6587',
          success: '#2BB673',
          warning: '#F6A623',
          error: '#E04F5F',
          surface: '#FFFFFF',
          background: '#F5F7FA',
        },
      },
    },
  },
});