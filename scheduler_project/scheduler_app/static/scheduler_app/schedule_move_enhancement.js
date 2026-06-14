(function () {
  "use strict";

  const schedule = document.querySelector("[data-schedule-container]");
  if (!schedule) {
    return;
  }

  let selectedForm = null;
  let selectedAssignmentId = null;

  function scheduleCells() {
    return schedule.querySelectorAll("[data-schedule-cell]");
  }

  function clearSelection() {
    scheduleCells().forEach(function (cell) {
      cell.classList.remove("schedule-cell-selected", "schedule-cell-valid-destination");
    });
    selectedForm = null;
    selectedAssignmentId = null;
  }

  function destinationCell(option) {
    const block = option.dataset.validDestinationBlock;
    const row = option.dataset.validDestinationRow;
    return Array.from(scheduleCells()).find(function (cell) {
      return cell.dataset.blockKey === block && cell.dataset.rowIndex === row;
    });
  }

  function selectAssignment(form) {
    clearSelection();
    selectedForm = form;

    const sourceCell = form.closest("[data-schedule-cell]");
    selectedAssignmentId = sourceCell.dataset.assignmentId;
    scheduleCells().forEach(function (cell) {
      if (cell.dataset.assignmentId === selectedAssignmentId) {
        cell.classList.add("schedule-cell-selected");
      }
    });

    form.querySelectorAll("[data-valid-destination-block]").forEach(function (option) {
      const cell = destinationCell(option);
      if (cell) {
        cell.classList.add("schedule-cell-valid-destination");
      }
    });
  }

  schedule.addEventListener("click", function (event) {
    const clickedCell = event.target.closest("[data-schedule-cell]");
    if (!clickedCell || !schedule.contains(clickedCell)) {
      clearSelection();
      return;
    }

    if (selectedForm && clickedCell.classList.contains("schedule-cell-valid-destination")) {
      const matchingOption = Array.from(
        selectedForm.querySelectorAll("[data-valid-destination-block]")
      ).find(function (option) {
        return destinationCell(option) === clickedCell;
      });
      if (matchingOption) {
        matchingOption.selected = true;
        selectedForm.requestSubmit();
      }
      return;
    }

    if (event.target.closest("form, details, summary, button, select, option, label, input")) {
      return;
    }

    const assignmentId = clickedCell.dataset.assignmentId;
    if (selectedAssignmentId && assignmentId === selectedAssignmentId) {
      clearSelection();
      return;
    }

    const linkedCellWithForm = Array.from(scheduleCells()).find(function (cell) {
      return (
        assignmentId &&
        cell.dataset.assignmentId === assignmentId &&
        cell.querySelector("[data-schedule-move-form]")
      );
    });
    const form = clickedCell.querySelector("[data-schedule-move-form]") || (
      linkedCellWithForm && linkedCellWithForm.querySelector("[data-schedule-move-form]")
    );
    if (form) {
      selectAssignment(form);
    } else {
      clearSelection();
    }
  });

  document.addEventListener("click", function (event) {
    if (!schedule.contains(event.target)) {
      clearSelection();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      clearSelection();
    }
  });
}());
