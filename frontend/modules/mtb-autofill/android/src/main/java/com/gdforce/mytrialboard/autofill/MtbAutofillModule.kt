package com.gdforce.mytrialboard.autofill

import android.os.Build
import android.view.autofill.AutofillManager
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

class MtbAutofillModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("MtbAutofill")

    Function("commit") {
      if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
        return@Function false
      }

      val activity = appContext.currentActivity ?: return@Function false
      activity.getSystemService(AutofillManager::class.java)?.commit()
      true
    }
  }
}
